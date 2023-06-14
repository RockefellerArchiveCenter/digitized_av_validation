import logging
import os
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from shutil import copytree, rmtree

import bagit
import boto3

logging.basicConfig(
    level=int(os.environ.get('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')
logging.getLogger("bagit").setLevel(logging.ERROR)


class ExtractError(Exception):
    pass


class AssetValidationError(Exception):
    pass


class FileFormatValidationError(Exception):
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
        if self.format not in ['audio', 'video']:
            raise Exception(f"Cannot process file with format {self.format}.")
        if not Path(self.tmp_dir).is_dir():
            raise Exception(f"Directory {self.tmp_dir} does not exist.")
        logging.debug(self.__dict__)

    def run(self):
        """Main method which calls all other logic."""
        logging.debug(
            f'Validation process started for {self.format} package {self.refid}.')
        try:
            extracted = Path(self.tmp_dir, self.refid)
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
        now = datetime.now()
        timestamp = now.timestamp()
        sts = boto3.client('sts', region_name=self.region)
        role = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=f'digitized-av-validation-{timestamp}')
        credentials = role['Credentials']
        client = boto3.client(
            resource,
            region_name=self.region,
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken'],)
        return client

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

    def validate_assets(self, bag_path):
        """Ensures that all expected files are present.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.

        Raises:
            AssetValidationError if expected file is missing.
        """
        suffix_map = [
            ("ma.wav", "Master"), ("a.mp3", "Access")] if self.format == 'audio' else [
            ("ma.mkv", "Master"), ("me.mov", "Mezzanine"), ("a.mp4", "Access")]
        for suffix, filetype in suffix_map:
            filename = f"{self.refid}_{suffix}"
            if not Path(bag_path, 'data', filename).is_file():
                raise AssetValidationError(
                    f"{filetype} file {filename} missing.")
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
            result = subprocess.call(
                ['mediaconch', '-p', policy_path, '-fs', f])
            if result != 0:
                error = subprocess.call(['mediaconch', f])
                raise FileFormatValidationError(
                    f"{str(f)} is not valid according to format policy: {error}")
        logging.debug(f'All file formats in {bag_path} are valid.')

    def move_to_destination(self, bag_path):
        """"Moves validated assets to destination directory.

        Args:
            bag_path (pathlib.Path): path of bagit Bag containing assets.
        """
        new_path = Path(self.destination_dir, self.refid)
        copytree(Path(bag_path, 'data'), new_path)
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
                    'StringValue': 'digitized_av_validation',
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
                    'StringValue': 'digitized_av_validation',
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'FAILURE',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': str(exception),
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
    sns_topic = os.environ.get('SNS_TOPIC')

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
