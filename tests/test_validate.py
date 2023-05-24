import json
import random
from pathlib import Path
from shutil import copyfile, copytree, rmtree
from unittest.mock import patch

import bagit
import boto3
import pytest
from moto import mock_s3, mock_sns, mock_sqs
from moto.core import DEFAULT_ACCOUNT_ID

from src.validate import (AssetValidationError, FileFormatValidationError,
                          Validator)

DEFAULT_ARGS = [
    'audio',
    'foo',
    '/qc',
    'b90862f3baceaae3b7418c78f9d50d52.tar.gz',
    '/validation',
    'topic']

VIDEO_ARGS = ['video',
              'foo',
              '/qc',
              '20f8da26e268418ead4aa2365f816a08.tar.gz',
              '/validation',
              'topic']


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Fixture to create and tear down dir before and after a test is run"""
    dir_list = [DEFAULT_ARGS[2], DEFAULT_ARGS[4]]
    for dir in dir_list:
        dir_path = Path(dir)
        if not dir_path.is_dir():
            dir_path.mkdir()

    yield  # this is where the testing happens

    for dir in dir_list:
        rmtree(dir)


def test_init():
    """Asserts Validator init method sets attributes correctly."""
    validator = Validator(*DEFAULT_ARGS)
    assert validator.format == 'audio'
    assert validator.source_bucket == 'foo'
    assert validator.destination_dir == '/qc'
    assert validator.source_filename == 'b90862f3baceaae3b7418c78f9d50d52.tar.gz'
    assert validator.tmp_dir == '/validation'
    assert validator.refid == 'b90862f3baceaae3b7418c78f9d50d52'

    invalid_args = ['text', 'foo', 'bar', 'baz.tar.gz', 'tmp']
    with pytest.raises(Exception):
        Validator(*invalid_args)


@patch('src.validate.Validator.download_bag')
@patch('src.validate.Validator.extract_bag')
@patch('src.validate.Validator.validate_bag')
@patch('src.validate.Validator.validate_assets')
@patch('src.validate.Validator.validate_file_formats')
@patch('src.validate.Validator.move_to_destination')
@patch('src.validate.Validator.cleanup_binaries')
@patch('src.validate.Validator.deliver_success_notification')
def test_run(mock_deliver, mock_cleanup, mock_move, mock_validate_formats,
             mock_validate_assets, mock_validate_bag, mock_extract_bag, mock_download):
    """Asserts correct methods are called by run method."""
    validator = Validator(*DEFAULT_ARGS)
    extracted_path = Path(validator.tmp_dir, validator.refid)
    download_path = "foo"
    mock_download.return_value = download_path
    validator.run()
    mock_deliver.assert_called_once_with()
    mock_cleanup.assert_called_once_with(extracted_path)
    mock_move.assert_called_once_with(extracted_path)
    mock_validate_formats.assert_called_once_with(extracted_path)
    mock_validate_assets.assert_called_once_with(extracted_path)
    mock_validate_bag.assert_called_once_with(extracted_path)
    mock_extract_bag.assert_called_once_with(download_path)
    mock_download.assert_called_once_with()


@patch('src.validate.Validator.download_bag')
@patch('src.validate.Validator.cleanup_binaries')
@patch('src.validate.Validator.deliver_failure_notification')
def test_run_with_exception(mock_deliver, mock_cleanup, mock_download):
    """Asserts run method handles exceptions correctly."""
    validator = Validator(*DEFAULT_ARGS)
    exception = Exception("Error downloading bag.")
    mock_download.side_effect = exception
    validator.run()
    mock_cleanup.assert_called_once()
    mock_deliver.assert_called_once_with(exception)


@mock_s3
def test_download_bag():
    """Asserts file is downloaded correctly."""
    validator = Validator(*DEFAULT_ARGS)
    bucket_name = validator.source_bucket
    expected_path = Path(validator.tmp_dir, validator.source_filename)
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket=bucket_name)
    s3.put_object(Bucket=bucket_name, Key=validator.source_filename, Body='')

    downloaded = validator.download_bag()
    assert downloaded == expected_path
    assert expected_path.is_file()


def test_extract_bag():
    """Asserts bag is extracted correctly and downloaded file is removed."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path(
        "tests",
        "fixtures",
        "b90862f3baceaae3b7418c78f9d50d52.tar.gz")
    tmp_path = Path(validator.tmp_dir, validator.source_filename)
    copyfile(fixture_path, tmp_path)

    validator.extract_bag(tmp_path)
    assert Path(validator.tmp_dir, validator.refid).is_dir()
    assert not tmp_path.is_file()


def test_validate_bag():
    """Asserts bag validation is successful or raises expected exceptions on failure."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path(
        "tests",
        "fixtures",
        "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    validator.validate_bag(tmp_path)

    rmtree(Path(tmp_path, 'data'))
    with pytest.raises(bagit.BagValidationError):
        validator.validate_bag(tmp_path)


def test_validate_assets():
    for args in [DEFAULT_ARGS, VIDEO_ARGS]:
        validator = Validator(*args)
        fixture_path = Path("tests", "fixtures", validator.refid)
        tmp_path = Path(validator.tmp_dir, validator.refid)
        copytree(fixture_path, tmp_path)

        validator.validate_assets(tmp_path)


def test_validate_assets_missing_file():
    for args in [DEFAULT_ARGS, VIDEO_ARGS]:
        validator = Validator(*args)
        fixture_path = Path("tests", "fixtures", validator.refid)
        tmp_path = Path(validator.tmp_dir, validator.refid)
        copytree(fixture_path, tmp_path)

        files = list(tmp_path.glob('data/*'))
        random.choice(files).unlink()

        with pytest.raises(AssetValidationError):
            validator.validate_assets(tmp_path)


@patch('src.validate.subprocess.call')
def test_validate_file_formats(mock_subprocess):
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path("tests", "fixtures", validator.refid)
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    mock_subprocess.return_value = 0
    validator.validate_file_formats(tmp_path)

    error_string = "This is an error!"
    mock_subprocess.side_effect = [1, error_string]
    with pytest.raises(FileFormatValidationError):
        error = validator.validate_file_formats(tmp_path)
        assert error_string in error


def test_move_to_destination():
    """Asserts correct file are moved to correct location."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path(
        "tests",
        "fixtures",
        "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    validator.move_to_destination(tmp_path)
    expected_paths = [
        f"{validator.destination_dir}/{validator.refid}/{validator.refid}_a.mp3",
        f"{validator.destination_dir}/{validator.refid}/{validator.refid}_ma.wav"]
    found = list(
        str(p) for p in Path(
            validator.destination_dir,
            validator.refid).glob('*'))
    assert len(expected_paths) == len(found)
    assert sorted(expected_paths) == sorted(found)


@mock_s3
def test_cleanup_binaries():
    """Asserts that binaries are cleaned up properly."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path(
        "tests",
        "fixtures",
        "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket=validator.source_bucket)

    copytree(fixture_path, tmp_path)
    s3.put_object(
        Bucket=validator.source_bucket,
        Key=validator.source_filename,
        Body='')

    validator.cleanup_binaries(tmp_path)
    assert not tmp_path.is_dir()
    found = s3.list_objects_v2(
        Bucket=validator.source_bucket,
        Prefix=validator.refid)['KeyCount']
    assert found == 0

    copytree(fixture_path, tmp_path)
    s3.put_object(
        Bucket=validator.source_bucket,
        Key=validator.source_filename,
        Body='')

    validator.cleanup_binaries(tmp_path, job_failed=True)
    assert not tmp_path.is_dir()
    found = s3.list_objects_v2(
        Bucket=validator.source_bucket,
        Prefix=validator.refid)['KeyCount']
    assert found == 1


@mock_sns
@mock_sqs
def test_deliver_success_notification():
    sns = boto3.client('sns', region_name='us-east-1')
    topic_arn = sns.create_topic(Name='my-topic')['TopicArn']
    sqs_conn = boto3.resource("sqs", region_name="us-east-1")
    sqs_conn.create_queue(QueueName="test-queue")
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="sqs",
        Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:test-queue",
    )

    default_args = DEFAULT_ARGS
    default_args[-1] = topic_arn
    validator = Validator(*default_args)

    validator.deliver_success_notification()

    queue = sqs_conn.get_queue_by_name(QueueName="test-queue")
    messages = queue.receive_messages(MaxNumberOfMessages=1)
    message_body = json.loads(messages[0].body)
    assert message_body['MessageAttributes']['format']['Value'] == validator.format
    assert message_body['MessageAttributes']['outcome']['Value'] == 'SUCCESS'
    assert message_body['MessageAttributes']['refid']['Value'] == validator.refid


@mock_sns
@mock_sqs
def test_deliver_failure_notification():
    sns = boto3.client('sns', region_name='us-east-1')
    topic_arn = sns.create_topic(Name='my-topic')['TopicArn']
    sqs_conn = boto3.resource("sqs", region_name="us-east-1")
    sqs_conn.create_queue(QueueName="test-queue")
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="sqs",
        Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:test-queue",
    )

    default_args = DEFAULT_ARGS
    default_args[-1] = topic_arn
    validator = Validator(*default_args)
    exception_message = "foo"
    exception = Exception(exception_message)

    validator.deliver_failure_notification(exception)

    queue = sqs_conn.get_queue_by_name(QueueName="test-queue")
    messages = queue.receive_messages(MaxNumberOfMessages=1)
    message_body = json.loads(messages[0].body)
    assert message_body['MessageAttributes']['format']['Value'] == validator.format
    assert message_body['MessageAttributes']['outcome']['Value'] == 'FAILURE'
    assert message_body['MessageAttributes']['refid']['Value'] == validator.refid
    assert message_body['MessageAttributes']['message']['Value'] == exception_message
