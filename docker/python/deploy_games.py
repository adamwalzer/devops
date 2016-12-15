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


class ProgressPercentage(object):
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


class CMWNDeploy:
    """Deploy class for games"""
    mime_maps = {
        'css': 'text/css',
        'js.map': 'application/javascript',
        'js': 'application/javascript'
    }

    def __init__(self):
        self.logger = None
        self.mk_logger()
        self.files_to_deploy = []
        self.s3 = boto3.resource('s3')
        self.allowed_environments = {
            'rc': 'staging',
            'master': 'qa',
            'qa': 'qa',
            'production': 'production',
            'demo': 'demo'
        }

        self.objects_on_s3 = {}
        self.current_branch = self.get_current_branch()

        default_environment = None
        if self.current_branch in self.allowed_environments:
            default_environment = self.allowed_environments[self.current_branch]

        parser = argparse.ArgumentParser(description='Deploys skribble to to environment', prog='deploy')
        parser.add_argument('-g', '--game', help='Deploy game')
        parser.add_argument('-b', '--bucket', help='Target bucket', default='cmwn-games')
        parser.add_argument('-e', '--env', help='Deploy to environment', default=default_environment,
                            choices=self.allowed_environments.values())
        parser.add_argument('-v', '--verbose', help='Turn on debugging logging', action='store_true')
        parser.add_argument('-P', '--prune', help='Remove files from s3 that are not local', action='store_true')
        parser.add_argument('-f', '--force', help='Force deploy even if file has not changed', action='store_true')

        self.args = parser.parse_args()

        if self.args.verbose:
            self.logger.setLevel(logging.DEBUG)

        self.logger.debug('Turning on debug')
        self.dest_dir = self.get_destination_directory()
        self.source_dir = self.get_source_directory()

        self.logger.info('Deploying %s to %s' % (self.source_dir, self.dest_dir))
        self.bucket = self.s3.Bucket(self.args.bucket)
        self.get_current_keys_on_s3()

        self.get_files_to_deploy()

        self.logger.info('Uploading %s files to S3' % len(self.files_to_deploy))
        for deploy_file_name in self.files_to_deploy:
            self.push_to_s3(deploy_file_name)

        if self.args.prune is True:
            self.logger.info('Pruning files')
            self.prune_files()

        print ('Deploy is complete')

    def get_current_keys_on_s3(self):
        """Fetches all the current keys on S3 along with their hashes"""
        self.logger.info('Fetching current files on S3')
        s3_objects = self.bucket.objects.filter(Prefix=self.dest_dir)
        for s3_object in s3_objects:
            s3_key = s3_object.key
            s3_etag = s3_object.e_tag.replace('"', "")
            self.logger.debug('Found %s with tag %s' % (s3_key, s3_etag))
            self.objects_on_s3[s3_key] = s3_etag

    def get_files_to_deploy(self):
        """Builds a list of files to deploy"""
        self.logger.info('Building file list')
        for real_dir, dir_name, file_names in os.walk(self.source_dir, topdown=True):
            base_dir = real_dir.replace(os.getcwd() + '/', "")
            test = [self.filter_file(file_name, base_dir) for file_name in file_names]
            self.files_to_deploy += filter(lambda v: v is not None, test)

    def filter_file(self, filter_file_name, path):
        """Filters out files that do not need to be deployed"""
        check_file = os.path.join(path, filter_file_name)
        if check_file.startswith('.git'):
            self.logger.debug('Skipping git folder')
            return

        # TODO Add a warning if the file name has crap characters
        self.logger.debug('Checking file %s' % check_file)
        check_ignore = subprocess.Popen(['git', 'ls-files', '--error-unmatch', '--exclude-standard', str(check_file)],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        check_ignore_status = check_ignore.wait()
        # 1 status code means the file is not committed or ignored
        if check_ignore_status is 1:
            self.logger.warn('File %s is not matched by git' % check_file)
            return

        # 0 means the file is in git
        if check_ignore_status is not 0:
            self.logger.critical('Git returned a bad status code when checking file: %s' % check_file)
            sys.exit(8)

        local_changed = self.compare_file_to_s3(check_file)
        if local_changed is False:
            self.logger.debug('File %s has not changed on s3' % check_file)
            return

        self.logger.info('Adding file %s' % check_file)
        return check_file

    def compare_file_to_s3(self, local_file):
        """Compares local file to file up on s3"""
        self.logger.debug('Comparing file: %s' % local_file)
        if self.args.force is True:
            self.logger.debug('File %s is going to be force pushed' % local_file)
            return True

        s3_key = os.path.join(self.args.env, local_file)
        self.logger.debug('Expected s3 key: %s' % s3_key)
        # print(self.objects_on_s3)
        if s3_key not in self.objects_on_s3:
            self.logger.debug('File %s has not been deployed yet' % local_file)
            return True

        local_hash = self.get_md5(local_file)
        remote_hash = self.objects_on_s3[s3_key]
        self.logger.debug('local hash: %s' % local_hash)
        self.logger.debug('remote hash: %s' % remote_hash)
        return local_hash != remote_hash

    def get_source_directory(self):
        """Gets the source directory to sync up"""
        base_dir = os.getcwd() + '/'
        self.logger.debug('Base dir: %s' % base_dir)
        self.logger.debug('Game parameter: %s' % self.args.game)
        if self.args.game is None:
            return base_dir

        base_dir += self.args.game
        self.logger.debug('Game directory: %s' % base_dir)
        if os.path.exists(base_dir) is False:
            self.logger.critical('Invalid game: %s' % self.args.game)
            sys.exit(128)

        return base_dir

    def get_destination_directory(self):
        """Sets the destination "directory" key"""
        if self.args.env in self.allowed_environments:
            return self.allowed_environments[self.args.env] + '/'
        elif self.args.env in self.allowed_environments.values():
            return self.args.env + '/'

        self.logger.critical('Cannot deploy to %s' % self.args.env)
        sys.exit(128)

    def get_current_branch(self):
        """Gets the current branch we are on"""
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

    def get_md5(self, filename):
        """Gets the MD5 hash of a file"""
        read_file = open(filename, 'rb')
        md5 = hashlib.md5()
        while True:
            data = read_file.read(10240)
            if len(data) == 0:
                break
            md5.update(data)

        read_file.close()
        return md5.hexdigest()

    def push_to_s3(self, source_file):
        """Pushes the file up to aws"""
        dest_file = os.path.join(self.dest_dir, source_file.replace(self.dest_dir, ""))

        source_mime = magic.from_file(source_file, mime=True)
        for extension in self.mime_maps:
            if source_file.endswith(extension):
                source_mime = self.mime_maps[extension]

        self.logger.debug('The mime of %s is %s' % (source_file, source_mime))

        self.s3.meta.client.upload_file(
            Filename=os.path.join(os.getcwd(), source_file),
            Bucket=self.args.bucket,
            Key=dest_file,
            ExtraArgs={'ACL': 'public-read','ContentType': source_mime},
            Callback=ProgressPercentage(os.path.join(os.getcwd(), source_file))
        )

    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def prune_files(self):
        """Removes files from S3 that are not local"""
        self.logger.info('Pruning files on s3')
        s3_files_to_remove = []
        for key in self.objects_on_s3:
            self.logger.debug('Checking if %s is local' % key)
            s3_file_name = key.replace(self.dest_dir + '/', '')
            if os.path.isfile(s3_file_name) is False:
                self.logger.warn('Adding %s to be removed' % s3_file_name)
                s3_files_to_remove.append({"Key": s3_file_name})

        if len(s3_files_to_remove) < 1:
            self.logger.info('No files to remove on s3')
            return

        for batch in self.chunks(s3_files_to_remove, 1000):
            s3_delete_result = self.bucket.delete_objects(
                Delete={
                    'Objects': batch
                }
            )

            if s3_delete_result.Errors is None or len(s3_delete_result.Errors) < 1:
                self.logger.debug('Deleted batch')
                continue

            self.logger.critical('Errors were found when deleting this batch!')

            for error in s3_delete_result.Errors:
                self.logger.critical("\tKey: %s \n\tError: %s" % (error.Key, error.Message))

        self.logger.info('Pruning complete')

    def mk_logger(self):
        """Creates a logger"""
        self.logger = logging.getLogger(__name__)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(message)s'))
        if have_colorlog & os.isatty(2):
            cf = colorlog.ColoredFormatter('%(log_color)s' + '%(message)s',
                                           log_colors={'DEBUG': 'reset', 'INFO': 'bold_blue',
                                                       'WARNING': 'yellow', 'ERROR': 'bold_red',
                                                       'CRITICAL': 'bold_red'})
            ch.setFormatter(cf)

        self.logger.addHandler(ch)
        self.logger.setLevel(logging.INFO)


CMWNDeploy()
