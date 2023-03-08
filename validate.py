import os


class ExtractError(Exception):
    pass


class BagValidationError(Exception):
    pass


class AssetValidationError(Exception):
    pass


class FileFormatValidationError(Exception):
    pass


class NotificationError(Exception):
    pass


class Validator(object):
    def __init__(self):
        self.source_bucket = os.environ.get('SOURCE_BUCKET')
        self.destination_bucket = os.environ.get('DESTINATION_BUCKET')
        self.source_filename = os.environ.get('SOURCE_FILENAME')

    def run(self):
        try:
            self.extract_bag()
            self.validate_bag()
            self.validate_assets()
            self.validate_file_formats()
            self.deliver_success_notification()
        except Exception as e:
            self.deliver_failure_notification(e)
            # TODO handle asset cleanup based on exception type

    def extract_bag(self):
        pass

    def validate_bag(self):
        pass

    def validate_assets(self):
        pass

    def validate_file_formats(self):
        pass

    def deliver_success_notifications(self):
        pass

    def deliver_failure_notifications(self, exception):
        pass


if __name__ == '__main__':
    Validator().run()
