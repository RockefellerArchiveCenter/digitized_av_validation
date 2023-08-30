import logging
import os
import re
import subprocess
import tarfile
import traceback
from pathlib import Path
from shutil import copytree, rmtree

import bagit
import boto3
from aws_assume_role_lib import assume_role

logging.basicConfig(
    level=int(os.environ.get('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')
logging.getLogger("bagit").setLevel(logging.ERROR)


class RefidError(Exception):
    pass


class ExtractError(Exception):
    pass


class AssetValidationError(Exception):
    pass


class FileFormatValidationError(Exception):
    pass


class AlreadyExistsError(Exception):
    pass


class Validator(object):
    """Validates digitized audio and moving image assets."""

    def __init__(self, region, role_arn, format, source_bucket,
                 destination_dir, source_filename, tmp_dir, sns_topic):
        self.role_arn = role_arn
        self.region = region
        self.format = format
        self.source_bucket = source_bucket
        self.destination_dir = destination_dir
        self.source_filename = source_filename
        self.refid = Path(source_filename).stem.split('.')[0]
        self.tmp_dir = tmp_dir
        self.sns_topic = sns_topic
        self.service_name = 'digitized_av_validation'
        if self.format not in ['audio', 'video']:
            raise Exception(f"Cannot process file with format {self.format}.")
        if not Path(self.tmp_dir).is_dir():
            Path(self.tmp_dir).mkdir(parents=True)
        logging.debug(self.__dict__)

    def run(self):
        """Main method which calls all other logic."""
        logging.debug(
            f'Validation process started for {self.format} package {self.refid}.')
        try:
            extracted = Path(self.tmp_dir, self.refid)
            self.validate_refid(self.refid)
            downloaded = self.download_bag()
            self.extract_bag(downloaded)
            self.validate_bag(extracted)
            self.validate_assets(extracted)
            self.validate_file_formats(extracted)
            self.move_to_destination(extracted)
            self.cleanup_binaries(extracted)
            self.deliver_success_notification()
            logging.info(
                f'{self.format} package {self.refid} successfully validated.')
        except Exception as e:
            logging.exception(e)
            self.cleanup_binaries(extracted, job_failed=True)
            self.deliver_failure_notification(e)

    def get_client_with_role(self, resource, role_arn):
        """Gets Boto3 client which authenticates with a specific IAM role."""
        session = boto3.Session()
        assumed_role_session = assume_role(session, role_arn)
        return assumed_role_session.client(resource)

    def validate_refid(self, refid):
        valid = re.compile(r"^[a-zA-Z0-9]{32}$")
        if not bool(valid.match(refid)):
            raise RefidError(f"{refid} is not a valid refid.")
        return True

    def download_bag(self):
        """Downloads a streaming file from S3.

        Returns:
            downloaded_path (pathlib.Path): path of the downloaded file.
        """
        downloaded_path = Path(self.tmp_dir, self.source_filename)
        client = self.get_client_with_role('s3', self.role_arn)
        transfer_config = boto3.s3.transfer.TransferConfig(
            multipart_threshold=1024 * 25,
            max_concurrency=10,
            multipart_chunksize=1024 * 25,
            use_threads=True)
        client.download_file(
            self.source_bucket,
            self.source_filename,
            downloaded_path,
            Config=transfer_config)
        logging.debug(f'Package downloaded to {downloaded_path}.')
        return downloaded_path

    def extract_bag(self, file_path):
        """Extracts the contents of a TAR file.

        Args:
            file_path (pathlib.Path): path of compressed file to extract.
        """
        try:
            tf = tarfile.open(file_path, "r:*")
            tf.extractall(self.tmp_dir)
            tf.close()
            file_path.unlink()
            logging.debug(f'Package {file_path} extracted to {self.tmp_dir}.')
        except Exception as e:
            raise ExtractError("Error extracting TAR file: {}".format(e))

    def validate_bag(self, bag_path):
        """Validates a bag.

        Args:
            bag_path (pathlib.Path): path of bagit Bag to validate.

        Raises:
            bagit.BagValidationError with the error in the `details` property.
        """
        bag = bagit.Bag(str(bag_path))
        bag.validate()
        logging.debug(f'Bag {bag_path} validated.')

    def get_expected_structure(self, master_files):
        """Return the files expected to be present in a bag's payload directory.

        Returns:
            expected_structure (list of strings): filenames expected to be present.
        """
        if self.format == 'audio':
            if len(master_files) > 1:
                expected_structure = [f"{self.refid}_a.mp3"]
                for i in range(1, len(master_files) + 1):
                    expected_structure.append(
                        f"{self.refid}_ma_{str(i).zfill(2)}.wav")
            else:
                expected_structure = [
                    f"{self.refid}_a.mp3",
                    f"{self.refid}_ma.wav"]
        elif self.format == 'video':
            expected_structure = [
                f"{self.refid}_ma.mkv",
                f"{self.refid}_me.mov",
                f"{self.refid}_a.mp4"]
        return expected_structure

    def get_actual_structure(self, bag_path):
        """Return the files present in a bag's payload directory

        Args:
            bag_path (pathlib.Path): base directory of the bag

        Returns:
            actual_structure (list of strings): filenames found in bag dir
        """
        return [p.name for p in (bag_path / 'data').iterdir()]

    def get_master_files(self, bag_path):
        """Returns filepaths of master files in a bag.

        Args:
            bag_path (pathlib.Path): base directory of the bag

        Returns:
            master_files (list of pathlib.Path objects): filepaths of master files.
        """
        if self.format == 'audio':
            master_files = (bag_path / 'data').glob('*.wav')
        elif self.format == 'video':
            master_files = (bag_path / 'data').glob('*.mkv')
        return list(master_files)

    def validate_assets(self, bag_path):
        """Ensures that all expected files are present.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.

        Raises:
            AssetValidationError if files delivered do not match expected files.
        """
        master_files = self.get_master_files(bag_path)
        expected_files = self.get_expected_structure(master_files)
        actual_files = self.get_actual_structure(bag_path)
        if set(expected_files) != set(actual_files):
            expected_files_display = '<br>'.join(sorted(expected_files))
            actual_files_display = '<br>'.join(sorted(actual_files))
            raise AssetValidationError(
                f'The files delivered do not match what is expected.<br><br>Expected files:<br>{expected_files_display}<br><br>Actual files:<br>{actual_files_display}')
        logging.debug(f'Package {bag_path} contains all expected assets.')

    def get_policy_path(self, filepath):
        """Gets path to Mediaconch policy based on filepath extension.

        Args:
            filepath (Pathlib.path): filepath to parse

        Returns:
            policy_path (string): filepath of Mediaconch policy.
        """
        try:
            policy_map = {
                'a.mp3': 'RAC_Audio_A_MP3.xml',
                'ma.wav': 'RAC_Audio_MA_WAV.xml',
                'a.mp4': 'RAC_Video_A_MP4.xml',
                'ma.mkv': 'RAC_Video_MA_FFV1MKV.xml',
                'me.mov': 'RAC_Video_MEZZ_ProRes.xml', }
            policy = policy_map[str(filepath).split('_')[-1]]
            return str(Path('mediaconch_policies', policy))
        except KeyError:
            raise FileFormatValidationError(
                f'No Mediaconch policy found for file {filepath}.')

    def validate_file_formats(self, bag_path):
        """Ensures that files pass MediaConch validation rules.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.
        """
        for f in bag_path.glob('data/*'):
            policy_path = self.get_policy_path(f)
            process = subprocess.Popen(['mediaconch',
                                        '-p',
                                        policy_path,
                                        '-fs',
                                        f],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            out, _ = process.communicate()
            result = out.decode()
            if result.startswith('fail!'):
                raise FileFormatValidationError(
                    f"{str(f)} is not valid according to format policy: {result}")
        logging.debug(f'All file formats in {bag_path} are valid.')

    def move_to_destination(self, bag_path):
        """"Moves validated assets to destination directory.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.
        """
        new_path = Path(self.destination_dir, self.refid)
        try:
            copytree(Path(bag_path, 'data'), new_path)
        except FileExistsError:
            raise AlreadyExistsError(
                f'A package with refid {self.refid} is already waiting to be QCed.')
        logging.debug(
            f'All files in payload directory of {bag_path} moved to destination.')

    def cleanup_binaries(self, bag_path, job_failed=False):
        """Removes binaries after completion of successful or failed job.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.
        """
        client = self.get_client_with_role('s3', self.role_arn)
        if bag_path.is_dir():
            rmtree(bag_path)
        if not job_failed:
            client.delete_object(
                Bucket=self.source_bucket,
                Key=self.source_filename)
        logging.debug('Binaries cleaned up.')

    def deliver_success_notification(self):
        """Sends notifications after successful run."""
        client = self.get_client_with_role('sns', self.role_arn)
        client.publish(
            TopicArn=self.sns_topic,
            Message=f'{self.format} package {self.source_filename} successfully validated',
            MessageAttributes={
                'format': {
                    'DataType': 'String',
                    'StringValue': self.format,
                },
                'refid': {
                    'DataType': 'String',
                    'StringValue': self.refid,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'SUCCESS',
                }
            })
        logging.debug('Success notification sent.')

    def deliver_failure_notification(self, exception):
        """"Sends notifications when run fails.

        Args:
            exception (Exception): the exception that was thrown.
        """
        client = self.get_client_with_role('sns', self.role_arn)
        tb = '\n\n'.join(traceback.format_exception(exception))
        client.publish(
            TopicArn=self.sns_topic,
            Message=f'{self.format} package {self.source_filename} is invalid',
            MessageAttributes={
                'format': {
                    'DataType': 'String',
                    'StringValue': self.format,
                },
                'refid': {
                    'DataType': 'String',
                    'StringValue': self.refid,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'FAILURE',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': f'{str(exception)}<br><br>{tb}',
                }
            })
        logging.debug('Failure notification sent.')


if __name__ == '__main__':
    region = os.environ.get('AWS_REGION')
    role_arn = os.environ.get('AWS_ROLE_ARN')
    format = os.environ.get('FORMAT')
    source_bucket = os.environ.get('AWS_SOURCE_BUCKET')
    source_filename = os.environ.get('SOURCE_FILENAME')
    tmp_dir = os.environ.get('TMP_DIR')
    destination_dir = os.environ.get('DESTINATION_DIR')
    sns_topic = os.environ.get('AWS_SNS_TOPIC')

    logging.debug(
        f'Validator instantiated with arguments: {region} {role_arn} {format} {source_bucket} {destination_dir} {source_filename} {tmp_dir} {sns_topic}')
    Validator(
        region,
        role_arn,
        format,
        source_bucket,
        destination_dir,
        source_filename,
        tmp_dir,
        sns_topic).run()
