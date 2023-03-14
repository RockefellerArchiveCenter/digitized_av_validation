import os
import tarfile
from pathlib import Path
from shutil import rmtree

import bagit
import boto3


class ExtractError(Exception):
    pass


class AssetValidationError(Exception):
    pass


class FileFormatValidationError(Exception):
    pass


class Validator(object):
    """Validates digitized audio and moving image assets."""

    def __init__(self, format, source_bucket,
                 destination_bucket, source_filename, tmp_dir):
        self.format = format
        self.source_bucket = source_bucket
        self.destination_bucket = destination_bucket
        self.source_filename = source_filename
        self.refid = Path(source_filename).stem.split('.')[0]
        self.tmp_dir = tmp_dir
        # TODO will need better instantiation here which passes credentials
        self.eventbridge = boto3.client('events')
        self.s3 = boto3.client('s3')
        self.transfer_config = boto3.s3.transfer.TransferConfig(
            multipart_threshold=1024 * 25,
            max_concurrency=10,
            multipart_chunksize=1024 * 25,
            use_threads=True)
        if self.format not in ['audio', 'moving image']:
            raise Exception(f"Cannot process file with format {self.format}.")
        if not Path(self.tmp_dir).is_dir():
            raise Exception(f"Directory {self.tmp_dir} does not exist.")

    def run(self):
        """Main method which calls all other logic."""
        try:
            extracted = Path(self.tmp_dir, self.refid)
            downloaded = self.download_bag()
            self.extract_bag(downloaded)
            self.validate_bag(extracted)
            self.validate_assets(extracted)
            self.validate_file_formats(extracted)
            self.move_to_destination(extracted)
            self.cleanup_successful_job(extracted)
            self.deliver_success_notification()
        except Exception as e:
            self.cleanup_failed_job(extracted)
            self.deliver_failure_notification(e)

    def download_bag(self):
        """Downloads a streaming file from S3."""
        downloaded_path = Path(self.tmp_dir, self.source_filename)
        self.s3.download_file(
            self.source_bucket,
            self.source_filename,
            downloaded_path,
            Config=self.transfer_config)
        return downloaded_path

    def extract_bag(self, file_path):
        """Extracts the contents of a TAR file."""
        try:
            tf = tarfile.open(file_path, "r:*")
            tf.extractall(self.tmp_dir)
            tf.close()
            file_path.unlink()
        except Exception as e:
            raise ExtractError("Error extracting TAR file: {}".format(e))

    def validate_bag(self, bag_path):
        """Validates a bag.

        Raises a bagit.BagValidationError with the error in the `details` property.
        """
        bag = bagit.Bag(str(bag_path))
        bag.validate()

    def validate_assets(self, bag_path):
        """Ensures that all expected files are present."""
        suffix_map = [
            ("ma.wav", "Master"), ("a.mp3", "Access")] if self.format == 'audio' else [
            ("ma.mov", "Master"), ("me.mov", "Mezzanine"), ("a.mp4", "Access")]
        for suffix, filetype in suffix_map:
            filename = f"{self.refid}_{suffix}"
            if not Path(bag_path, 'data', filename).is_file():
                raise AssetValidationError(
                    f"{filetype} file {filename} missing.")

    def validate_file_formats(self, bag_path):
        """Ensures that files pass MediaConch validation rules."""
        # validation_rules = {} if self.format == 'audio' else {}
        pass

    def get_content_type(self, extension):
        format_map = {
            ".mov": "video/quicktime",
            ".mp4": "video/mp4",
            ".wav": "audio/x-wav",
            ".mp3": "audio/mpeg"}
        try:
            return format_map[extension]
        except KeyError:
            raise Exception(
                f"Unable to upload asset with unknown extension {extension}.")

    def move_to_destination(self, bag_path):
        """"Uploads directory to destination S3 bucket."""
        for path_obj in bag_path.glob('data/*'):
            self.s3.upload_file(
                path_obj,
                self.destination_bucket,
                f"{self.refid}/{path_obj.name}",
                ExtraArgs={
                    'ContentType': self.get_content_type(
                        path_obj.suffix)},
                Config=self.transfer_config)

    def cleanup_successful_job(self, bag_path):
        """Removes artifacts from successful job."""
        if bag_path.is_dir():
            rmtree(bag_path)
        self.s3.delete_object(
            Bucket=self.source_bucket,
            Key=self.source_filename)

    def cleanup_failed_job(self, bag_path):
        """Removes artifacts from failed job."""
        if bag_path.is_dir():
            rmtree(bag_path)
        to_delete = self.s3.list_objects(
            Bucket=self.destination_bucket,
            Prefix=self.refid).get('Contents', [])
        self.s3.delete_objects(
            Bucket=self.destination_bucket,
            Delete={'Objects': [{'Key': k['Key']} for k in to_delete]})

    def deliver_success_notification(self):
        """Sends notifications after successful run."""
        # TODO: figure out notifications approach.
        pass

    def deliver_failure_notification(self, exception):
        """"Sends notifications when run fails."""
        # TODO: figure out notifications approach.
        pass


if __name__ == '__main__':
    format = os.environ.get('FORMAT')
    source_bucket = os.environ.get('SOURCE_BUCKET')
    destination_bucket = os.environ.get('DESTINATION_BUCKET')
    source_filename = os.environ.get('SOURCE_FILENAME')
    tmp_dir = os.environ.get('TMP_DIR')
    Validator(
        format,
        source_bucket,
        destination_bucket,
        source_filename,
        tmp_dir).run()
