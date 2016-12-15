#!/usr/bin/env python
"""
Syncs a folder up to S3 for games
"""

import logging  # logging
import os  # Operating system functions
import sys  # System functions
import argparse  # parse args from the command line
import subprocess  # makes system calls
import hashlib  # used to compare files
import threading  # used to display a progress bar with out blocking
import json  # Used to parse JSON Strings

try:
    import boto3  # Aws API
except ImportError:
    print ('You are missing boto3.  run: pip install boto3')
    sys.exit(1)

try:
    import magic  # Mime type detector
except ImportError:
    print ('You are missing boto3.')
    print ('to fix run: brew install libmagic')
    print ('then: pip install python-magic')
    sys.exit(1)

try:
    import colorlog  # makes the logs nice and colorful in the console

    have_colorlog = True
except ImportError:
    have_colorlog = False
    pass

logger = logging.getLogger(__name__)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(message)s'))
if have_colorlog & os.isatty(2):
    cf = colorlog.ColoredFormatter('%(log_color)s' + '%(message)s',
                                   log_colors={'DEBUG': 'reset', 'INFO': 'bold_blue',
                                               'WARNING': 'yellow', 'ERROR': 'bold_red',
                                               'CRITICAL': 'bold_red'})
    ch.setFormatter(cf)

logger.addHandler(ch)
logger.setLevel(logging.INFO)


def get_md5(filename):
    """
    Gets the MD5 hash of a file

    :param filename:
    :return:
    """
    read_file = open(filename, 'rb')
    md5 = hashlib.md5()
    while True:
        data = read_file.read(10240)
        if len(data) == 0:
            break
        md5.update(data)

    read_file.close()
    return md5.hexdigest()


def chunks(l, n):
    """
    Yield successive n-sized chunks from l.

    :param l:
    :param n:
    :return:
    """
    for i in range(0, len(l), n):
        yield l[i:i + n]


class ProgressPercentage(object):
    """
    Displays a nice percentage bar when uploading
    """

    def __init__(self, filename):
        self._filename = os.path.basename(filename)
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            sys.stdout.write(
                "\r%s  %s / %s  (%.2f%%)" % (
                    self._filename, self._seen_so_far, self._size,
                    percentage))
            sys.stdout.flush()


class CMWNDeploy(object):
    """
    Deploy class for games
    """
    mime_maps = {
        'css': 'text/css',
        'js.map': 'application/javascript',
        'js': 'application/javascript'
    }

    links_ref = {
        'rc': '_STAGING',
        'qa': '_QA',
        'production': '_LATEST',
        'demo': '_DEMO'
    }

    def __init__(self):
        self.files_to_deploy = []
        self.s3 = boto3.resource('s3')

        # Load in the command line arguments
        args = self._parse_cli()
        self.cache_time = args.cache_time
        self.source_dir = args.source
        self.bucket_name = args.bucket
        self.package_file = args.package_file
        self.version = self._get_version_from_file(args.version)
        self.link = self._get_link_directory(args.link)
        self.link_only = args.link_only
        self.bucket = self.s3.Bucket(self.bucket_name)

        if self.link_only is False:
            logger.info('Deploying %s to %s' % (self.source_dir, self.bucket_name))

            self._check_version_on_s3()
            self._get_files_to_deploy()

            logger.info('Uploading %s files to S3' % len(self.files_to_deploy))
            for deploy_file_name in self.files_to_deploy:
                self._push_to_s3(deploy_file_name)

        if self.link is not None:
            self._link()

        print ('Deploy is complete')

    def _get_link_directory(self, link):
        """
        Sets the destination "directory" key

        :return:
        """
        if link in self.links_ref:
            return self.links_ref[link]
        elif link in self.links_ref.values():
            return link

        raise SystemExit('Cannot link to %s' % link)

    def _link(self):
        """
        Links the version to an environment

        :return:
        """
        s3_objects = self.bucket.objects.filter(Prefix=self.version)
        for s3_object in s3_objects:
            copy_source = {
                'Bucket': self.bucket_name,
                'Key': s3_object.key
            }

            dest_key = str(s3_object.key).replace(self.version + '/', self.link + '/')
            logger.info('Linking %s to %s' % (copy_source, dest_key))
            self.bucket.copy(
                copy_source,
                Key=dest_key,
                ExtraArgs={
                    'ACL': 'public-read',
                },
            )

    @staticmethod
    def _parse_cli():
        """
        Gets the CLI parameters

        :return:
        """
        # Set some defaults from the environment variables
        package_file = os.getenv('PACKAGE_FILE')
        bucket_name = os.getenv('BUCKET_NAME')

        parser = argparse.ArgumentParser(description='Deploys skribble to to environment', prog='deploy')
        parser.add_argument('--version',
                            help='Force setting the version number instead of reading from packageFile',
                            default=None)

        parser.add_argument('-s', '--source', help='Source Directory', default='build')
        parser.add_argument('--package-file', help='Version', default=package_file)
        parser.add_argument('--link', help='Link this version to an environment')
        parser.add_argument('--link-only', help="Only create a link to version", action='store_true')
        parser.add_argument('-ct', '--cache-time', help='Time for cache', default='86400')
        parser.add_argument('--bucket', help='Force to this bucket', default=bucket_name)
        parser.add_argument('-v', '--verbose', help='Turn on debugging logging', action='store_true')

        args = parser.parse_args()
        if args.verbose:
            logger.setLevel(logging.DEBUG)
            logger.debug('Turning on debug')

        logger.debug(args)
        return args

    def _get_version_from_file(self, force_version=None):
        """
        Gets the version number to use for deploy

        :param force_version:
        :return:
        """
        logger.debug('Current force_version %s' % force_version)
        if force_version is not None:
            return force_version

        logger.debug('Current self.package_file %s' % self.package_file)
        dir_path = os.path.dirname(os.path.realpath(__file__))

        good_json = open(dir_path + "/" + self.package_file).read()
        package_version = json.loads(good_json)['version']
        logger.debug('Package file version %s' % package_version)
        return package_version

    def _check_version_on_s3(self):
        """
        Checks if the version is on S3 or not

        :return:
        """
        logger.info('Fetching current files on S3')
        s3_objects = self.bucket.objects.filter(Prefix=self.version)
        for s3_object in s3_objects:
            raise SystemExit('This version is already deployed to S3 please bump the version number')

    def _get_files_to_deploy(self):
        """
        Builds a list of files to deploy

        :return:
        """
        logger.info('Build list of files to deploy')
        for real_dir, dir_name, file_names in os.walk(self.source_dir, topdown=True):
            base_dir = real_dir.replace(os.getcwd() + '/', "")
            test = [self._filter_file(file_name, base_dir) for file_name in file_names]
            self.files_to_deploy += filter(lambda v: v is not None, test)

    @staticmethod
    def _filter_file(filter_file_name, path):
        """
        Filters out files that do not need to be deployed

        :param filter_file_name:
        :param path:
        :return:
        """
        check_file = os.path.join(path, filter_file_name)
        # Skip the .git folder
        if check_file.startswith('.git'):
            logger.debug('Skipping git folder')
            return

        # TODO Add a warning if the file name has crap characters
        logger.debug('Checking file %s' % check_file)
        return check_file

    def _push_to_s3(self, source_file):
        """
        Pushes the file up to aws

        :param source_file:
        :return:
        """
        dest_file = str(source_file).replace(self.source_dir, self.version)

        source_mime = magic.from_file(source_file, mime=True)
        for extension in self.mime_maps:
            if source_file.endswith(extension):
                source_mime = self.mime_maps[extension]

        logger.debug('The mime of %s is %s' % (source_file, source_mime))
        logger.debug('Uploading: %s' % os.path.join(os.getcwd(), source_file))
        logger.debug('Destination: %s' % dest_file)

        self.s3.meta.client.upload_file(
            Filename=os.path.join(os.getcwd(), source_file),
            Bucket=self.bucket.name,
            Key=dest_file,
            ExtraArgs={
                'ACL': 'public-read',
                'ContentType': source_mime,
                'CacheControl': 'max-age=%s' % self.cache_time
            },
            Callback=ProgressPercentage(os.path.join(os.getcwd(), source_file))
        )


CMWNDeploy()
