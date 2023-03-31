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

from validate import AssetValidationError, Validator

DEFAULT_ARGS = [
    'audio',
    'foo',
    'bar',
    'b90862f3baceaae3b7418c78f9d50d52.tar.gz',
    'tmp',
    'topic']


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Fixture to create and tear down tmp dir before and after a test is run"""
    tmp_dir = Path(DEFAULT_ARGS[4])
    if not tmp_dir.is_dir():
        tmp_dir.mkdir()

    yield  # this is where the testing happens

    rmtree(DEFAULT_ARGS[4])


def test_init():
    """Asserts Validator init method sets attributes correctly."""
    validator = Validator(*DEFAULT_ARGS)
    assert validator.format == 'audio'
    assert validator.source_bucket == 'foo'
    assert validator.destination_bucket == 'bar'
    assert validator.source_filename == 'b90862f3baceaae3b7418c78f9d50d52.tar.gz'
    assert validator.tmp_dir == 'tmp'
    assert validator.refid == 'b90862f3baceaae3b7418c78f9d50d52'

    invalid_args = ['text', 'foo', 'bar', 'baz.tar.gz', 'tmp']
    with pytest.raises(Exception):
        Validator(*invalid_args)


@patch('validate.Validator.download_bag')
@patch('validate.Validator.extract_bag')
@patch('validate.Validator.validate_bag')
@patch('validate.Validator.validate_assets')
@patch('validate.Validator.validate_file_formats')
@patch('validate.Validator.move_to_destination')
@patch('validate.Validator.cleanup_successful_job')
@patch('validate.Validator.deliver_success_notification')
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


@patch('validate.Validator.download_bag')
@patch('validate.Validator.cleanup_failed_job')
@patch('validate.Validator.deliver_failure_notification')
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
    fixture_path = Path("fixtures", "b90862f3baceaae3b7418c78f9d50d52.tar.gz")
    tmp_path = Path(validator.tmp_dir, validator.source_filename)
    copyfile(fixture_path, tmp_path)

    validator.extract_bag(tmp_path)
    assert Path(validator.tmp_dir, validator.refid).is_dir()
    assert not tmp_path.is_file()


def test_validate_bag():
    """Asserts bag validation is successful or raises expected exceptions on failure."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path("fixtures", "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    validator.validate_bag(tmp_path)

    rmtree(Path(tmp_path, 'data'))
    with pytest.raises(bagit.BagValidationError):
        validator.validate_bag(tmp_path)


def test_validate_assets():
    video_args = ['video',
                  'foo',
                  'bar',
                  '20f8da26e268418ead4aa2365f816a08.tar.gz',
                  'tmp',
                  'topic']
    for args in [DEFAULT_ARGS, video_args]:
        validator = Validator(*args)
        fixture_path = Path("fixtures", validator.refid)
        tmp_path = Path(validator.tmp_dir, validator.refid)
        copytree(fixture_path, tmp_path)

        validator.validate_assets(tmp_path)


def test_validate_assets_missing_file():
    video_args = ['video',
                  'foo',
                  'bar',
                  '20f8da26e268418ead4aa2365f816a08.tar.gz',
                  'tmp',
                  'topic']
    for args in [DEFAULT_ARGS, video_args]:
        validator = Validator(*args)
        fixture_path = Path("fixtures", validator.refid)
        tmp_path = Path(validator.tmp_dir, validator.refid)
        copytree(fixture_path, tmp_path)

        files = list(tmp_path.glob('data/*'))
        random.choice(files).unlink()

        with pytest.raises(AssetValidationError):
            validator.validate_assets(tmp_path)


def test_validate_file_formats():
    pass


@mock_s3
def test_move_to_destination():
    """Asserts correct file are moved to correct location."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path("fixtures", "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket=validator.destination_bucket)

    validator.move_to_destination(tmp_path)
    expected_paths = [
        f"{validator.refid}/{validator.refid}_a.mp3",
        f"{validator.refid}/{validator.refid}_ma.wav"]
    found = [o['Key'] for o in s3.list_objects_v2(
        Bucket=validator.destination_bucket,
        Prefix=validator.refid)['Contents']]
    assert len(expected_paths) == len(found)
    assert sorted(expected_paths) == sorted(found)


@mock_s3
def test_cleanup_successful_job():
    """Asserts that successful jobs are cleaned up properly."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path("fixtures", "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket=validator.source_bucket)
    s3.put_object(
        Bucket=validator.source_bucket,
        Key=validator.source_filename,
        Body='')

    validator.cleanup_successful_job(tmp_path)
    assert not tmp_path.is_dir()


@mock_s3
def test_cleanup_failed_job():
    """Asserts that failed jobs are cleaned up properly."""
    validator = Validator(*DEFAULT_ARGS)
    fixture_path = Path("fixtures", "b90862f3baceaae3b7418c78f9d50d52")
    tmp_path = Path(validator.tmp_dir, validator.refid)
    copytree(fixture_path, tmp_path)

    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket=validator.destination_bucket)
    s3.put_object(
        Bucket=validator.destination_bucket,
        Key=validator.refid,
        Body='')
    s3.put_object(Bucket=validator.destination_bucket,
                  Key=f"{validator.refid}/foo", Body='')

    validator.cleanup_failed_job(tmp_path)
    assert not tmp_path.is_dir()
    deleted = s3.list_objects(
        Bucket=validator.destination_bucket,
        Prefix=validator.refid).get('Contents', [])
    assert len(deleted) == 0


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
