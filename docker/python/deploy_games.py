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

    branch_map = {
        'rc': 'staging',
        'master': 'qa',
        'production': 'production',
        'demo': 'demo'
    }

    objects_on_s3 = {}

    def __init__(self):
        self.files_to_deploy = []
        self.s3 = boto3.resource('s3')
        self.current_branch = self._get_current_branch()

        default_environment = None
        if self.current_branch in self.branch_map:
            default_environment = self.branch_map[self.current_branch]

        # Load in the command line arguments
        args = self._parse_cli(default_environment, self.branch_map)
        self.prune = args.prune
        self.game = args.game
        self.env = args.env
        self.force = args.force
        self.cache_time = args.cache_time

        if self.env is None:
            raise SystemExit('You cannot deploy this branch with out the --env parameter')

        sub_domain = 'games-%s' % args.env
        if args.env == 'production':
            sub_domain = 'games'

        bucket_name = '%s.changemyworldnow.com' % sub_domain
        if args.bucket is not None:
            bucket_name = args.bucket

        self.source_dir = self._get_source_directory()

        logger.info('Deploying %s to %s' % (self.source_dir, bucket_name))
        self.bucket = self.s3.Bucket(bucket_name)
        self._get_current_keys_on_s3()
        self._get_files_to_deploy()

        logger.info('Uploading %s files to S3' % len(self.files_to_deploy))
        for deploy_file_name in self.files_to_deploy:
            self._push_to_s3(deploy_file_name)

        if self.prune is True:
            logger.info('Pruning files')
            self._prune_files()

        print ('Deploy is complete')

    @staticmethod
    def _parse_cli(default_environment, env_options):
        """
        Gets the parameters from the command line

        :param default_environment:
        :param env_options:
        :return:
        """
        parser = argparse.ArgumentParser(description='Deploys skribble to to environment', prog='deploy')
        parser.add_argument('-g', '--game', help='Deploy game', default='')
        parser.add_argument('-ct', '--cache-time', help='Time for cache', default='86400')
        parser.add_argument('--bucket', help='Force to this bucket')
        parser.add_argument('-e', '--env', help='Deploy to environment', default=default_environment,
                            choices=env_options.values())
        parser.add_argument('-v', '--verbose', help='Turn on debugging logging', action='store_true')
        parser.add_argument('-P', '--prune', help='Remove files from s3 that are not local', action='store_true')
        parser.add_argument('-f', '--force', help='Force deploy even if file has not changed', action='store_true')

        args = parser.parse_args()
        if args.verbose:
            logger.setLevel(logging.DEBUG)
            logger.debug('Turning on debug')

        return args

    def _get_current_keys_on_s3(self):
        """
        Fetches all the current keys on S3 along with their hashes

        :return:
        """
        logger.info('Fetching current files on S3')
        try:
            s3_objects = self.bucket.objects.filter(Prefix=self.game)
            for s3_object in s3_objects:
                s3_key = s3_object.key
                s3_etag = s3_object.e_tag.replace('"', "")
                logger.debug('Found %s with tag %s' % (s3_key, s3_etag))
                self.objects_on_s3[s3_key] = s3_etag
        except:
            pass

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

    def _filter_file(self, filter_file_name, path):
        """
        Filters out files that do not need to be deployed

        :param filter_file_name:
        :param path:
        :return:
        """
        check_file = os.path.join(path, filter_file_name)
        if check_file.startswith('.git'):
            logger.debug('Skipping git folder')
            return

        # TODO Add a warning if the file name has crap characters
        logger.debug('Checking file %s' % check_file)
        check_ignore = subprocess.Popen(['git', 'ls-files', '--error-unmatch', '--exclude-standard', str(check_file)],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        check_ignore_status = check_ignore.wait()
        # 1 status code means the file is not committed or ignored
        if check_ignore_status is 1:
            logger.warn('File %s is not matched by git' % check_file)
            return

        # 0 means the file is in git
        if check_ignore_status is not 0:
            logger.critical('Git returned a bad status code when checking file: %s' % check_file)
            sys.exit(8)

        local_changed = self._compare_file_to_s3(check_file)
        if local_changed is False:
            logger.debug('File %s has not changed on s3' % check_file)
            return

        logger.info('Adding file %s' % check_file)
        return check_file

    def _compare_file_to_s3(self, local_file):
        """
        Compares local file to file up on s3

        :param local_file:
        :return:
        """
        logger.debug('Comparing file: %s' % local_file)
        if self.force is True:
            logger.debug('File %s is going to be force pushed' % local_file)
            return True

        s3_key = local_file
        logger.debug('Expected s3 key: %s' % s3_key)
        # print(self.objects_on_s3)
        if s3_key not in self.objects_on_s3:
            logger.debug('File %s has not been deployed yet' % local_file)
            return True

        local_hash = get_md5(local_file)
        remote_hash = self.objects_on_s3[s3_key]
        logger.debug('local hash: %s' % local_hash)
        logger.debug('remote hash: %s' % remote_hash)
        return local_hash != remote_hash

    def _get_source_directory(self):
        """
        Gets the source directory to sync up

        :return:
        """
        base_dir = os.getcwd() + '/'
        logger.debug('Base dir: %s' % base_dir)
        logger.debug('Game parameter: %s' % self.game)
        if self.game == '':
            return base_dir

        base_dir += self.game
        logger.debug('Game directory: %s' % base_dir)
        if os.path.exists(base_dir) is False:
            logger.critical('Invalid game: %s' % self.game)
            sys.exit(128)

        return base_dir

    @staticmethod
    def _get_current_branch():
        """
        Gets the current branch we are on

        :return:
        """
        current_branch_cmd = subprocess.Popen(['git', 'branch', '-q'],
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE)

        current_branch_status = current_branch_cmd.wait()
        if current_branch_status != 0:
            exit(1)

        for branch_line in current_branch_cmd.stdout:
            if branch_line.startswith('*'):
                return branch_line.split('*')[1].strip()

        return None

    def _push_to_s3(self, source_file):
        """
        Pushes the file up to aws

        :param source_file:
        :return:
        """
        dest_file = source_file

        source_mime = magic.from_file(source_file, mime=True)
        for extension in self.mime_maps:
            if source_file.endswith(extension):
                source_mime = self.mime_maps[extension]

        logger.debug('The mime of %s is %s' % (source_file, source_mime))

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

    def _prune_files(self):
        """
        Removes files from S3 that are not local

        :return:
        """
        logger.info('Pruning files on s3')
        s3_files_to_remove = []
        for key in self.objects_on_s3:
            logger.debug('Checking if %s is local' % key)
            s3_file_name = key
            if os.path.isfile(s3_file_name) is False:
                logger.warn('Adding %s to be removed' % s3_file_name)
                s3_files_to_remove.append({"Key": s3_file_name})

        if len(s3_files_to_remove) < 1:
            logger.info('No files to remove on s3')
            return

        for batch in chunks(s3_files_to_remove, 1000):
            s3_delete_result = self.bucket.delete_objects(
                Delete={
                    'Objects': batch
                }
            )

            if s3_delete_result['Errors'] is None or len(s3_delete_result.Errors) < 1:
                logger.debug('Deleted batch')
                continue

            logger.critical('Errors were found when deleting this batch!')

            for error in s3_delete_result.Errors:
                logger.critical("\tKey: %s \n\tError: %s" % (error.Key, error.Message))

        logger.info('Pruning complete')


CMWNDeploy()
