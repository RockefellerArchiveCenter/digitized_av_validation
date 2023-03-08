import os

from validate import Validator


def test_init():
    """Asserts Validator init method sets attributes based on environment variables."""
    source_bucket = "foo"
    destination_bucket = "bar"
    source_filename = "barz"
    os.environ['SOURCE_BUCKET'] = source_bucket
    os.environ['DESTINATION_BUCKET'] = destination_bucket
    os.environ['SOURCE_FILENAME'] = source_filename
    validator = Validator()
    assert validator.source_bucket == source_bucket
    assert validator.destination_bucket == destination_bucket
    assert validator.source_filename == source_filename
