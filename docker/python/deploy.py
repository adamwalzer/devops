#!/usr/bin/env python
"""
Runs the deploy of code to AWS
"""

import logging  # logging
import os  # Operating system functions
import sys  # System functions
import argparse  # parse args from the command line
import time  # Sleep function

try:
    import boto3  # Aws API
except ImportError:
    print ('You are missing boto3.  run: pip install boto3')
    sys.exit(1)

try:
    import colorlog  # makes the logs nice and colorful in the console

    have_colorlog = True
except ImportError:
    have_colorlog = False
    pass


class CmwDeploy:
    def __init__(self):
        """Creates the boto3 resources, also sets up logging"""

        self.logger = None
        self.mk_logger()

        # set some defaults
        self.ec2 = boto3.resource('ec2')
        self.ssm = boto3.client('ssm')

        self.bastion_id = None
        self.valid_applications = ['api', 'front']
        self.valid_env = ['qa', 'staging', 'production', 'demo', 'lab']
        self.deploy_target = None

        default_ami = os.getenv('AWS_BASE_AMI');
        if default_ami is None:
            default_ami = 'ami-c481fad3' # Base Amazon AMI

        # load in arguments
        parser = argparse.ArgumentParser(description='Deploys skribble to to environment', prog='deploy')
        parser.add_argument('version', help='The version to deploy')
        parser.add_argument('app', help='Which app to deploy')
        parser.add_argument('env', help='Where to deploy the application')
        parser.add_argument('-t', '--target', help='run the deploy to target instance')
        parser.add_argument('-k', '--keep', help='Leave the instance running', action='store_true')
        parser.add_argument('--ami', help='AMI to use when creating instance', default=default_ami)
        parser.add_argument('--vpc', help='VPC id', default="vpc-00ef3867")
        parser.add_argument('--security-group', help='Security group to use when creating instance',
                            default="sg-906e08ea,sg-a5ef8ade,sg-33620449")
        parser.add_argument('--subnet', help='Subnet to assign new instance too', default="subnet-b8094ce0")
        parser.add_argument('-v', '--verbose', help='Turn on debugging logging', action='store_true')

        self.args = parser.parse_args()

        try:
            if self.args.verbose:
                self.logger.setLevel(logging.DEBUG)
                self.logger.debug('Turning on debug')

            self.logger.info(self.args)
            self.bastion_id = self.find_instance_by_name('Bastion')
            if self.bastion_id is None:
                self.logger.error('Bastion is missing')
                sys.exit(128)

            self.get_target()
            if self.deploy_target is None:
                self.logger.error('No Deploy target set')
                sys.exit(128)

            self.send_command()
            self.destroy_instance()
        except (KeyboardInterrupt, SystemExit):
            self.logger.info('Exit')
            self.destroy_instance(force=True)
            sys.exit(1)
        except:
            self.logger.critical('Exception happened')
            self.destroy_instance(force=True)
            raise

    def destroy_instance(self, force=False):
        """Destroys the instance"""
        if self.ec2 is None or self.deploy_target is None:
            self.logger.info('No instance to destroy')
            return

        if self.args.keep is True and force is False:
            self.logger.info('Not destroying instance')
            return

        self.logger.info('Terminating instance: %s' % self.deploy_target)
        self.logger.debug('Initiating Dalek Extermination protocol')
        self.ec2.meta.client.terminate_instances(InstanceIds=[self.deploy_target])
        self.logger.info('Instance terminated')
        self.logger.debug('EXTERMINATE EXTERMINATE!!')

    def get_target(self):
        """Finds the target to deploy too"""
        if self.args.target is not None:
            self.logger.debug('Target set from command line')
            return self.args.target

        return self.create_instance()

    def create_instance(self):
        """Creates a new instance to deploy too"""
        self.logger.info('Creating instance')
        security_groups = self.args.security_group.split(',')
        instance = self.ec2.create_instances(
            ImageId=self.args.ami,
            MinCount=1,
            MaxCount=1,
            KeyName='cmwn',
            SecurityGroupIds=security_groups,
            InstanceType='t2.micro',
            Monitoring={
                'Enabled': False
            },
            InstanceInitiatedShutdownBehavior='terminate',
            SubnetId=self.args.subnet
        )

        self.deploy_target = instance[0].id

        instance[0].create_tags(
            Tags=[
                {'Key': 'Name', 'Value': '%s Deploy' % (self.args.app[0].upper() + self.args.app[1:])},
                {'Key': 'Type', 'Value': '%s Build' % (self.args.app[0].upper() + self.args.app[1:])},
                {'Key': 'Environment', 'Value': self.args.env[0].upper() + self.args.env[1:]},
                {'Key': 'Web', 'Value': ''},
                {'Key': 'Php', 'Value': ''}
            ]
        )

        self.logger.info('Waiting for %s instance to start' % self.deploy_target)
        instance[0].wait_until_running()
        self.logger.debug('Instance in a started state')

    def find_instance_by_name(self, instance_name):
        """Checks to see if bastion is alive and running"""
        self.logger.info("Finding %s" % instance_name)

        instance_iterator = self.ec2.instances.filter(Filters=[{
            'Name': 'tag:Name',
            "Values": [instance_name]}])

        instance = None
        for instance in instance_iterator:
            break

        if instance is None:
            self.logger.warn('Could not find %s' % instance_name)
            return None

        self.logger.debug('Found %s with id: %s in a %s state' % (instance_name, instance.id, instance.state['Name']))

        if instance.state['Name'] != 'running':
            self.logger.info('Instance %s is not running' % instance_name)
            instance.start()
            instance.wait_until_running()

        return instance.id

    def send_command(self):
        """Sends the SSM command to bastion"""
        self.logger.info('Sending deploy command')
        command = self.ssm.send_command(
            InstanceIds=[
                self.bastion_id,
            ],
            DocumentName="CMWN-Deploy",
            Comment='string',
            Parameters={
                'deployTo': [
                    self.deploy_target,
                ],
                'version': [
                    self.args.version,
                ],
                'application': [
                    self.args.app
                ]
            },
            OutputS3BucketName='cmwn-logs',
            OutputS3KeyPrefix=('deploy/%s' % self.args.app),
        )

        self.logger.debug('Command Id: %s' % command['Command']['CommandId'])
        self.logger.info('Waiting for command to complete')
        status = 'Pending'
        while status == 'Pending' or status == 'InProgress':
            time.sleep(3)
            status = (self.ssm.list_commands(CommandId=command['Command']['CommandId']))['Commands'][0]['Status']
            self.logger.debug('Current command status: %s' % status)

        if status != 'Success':
            self.logger.error('Command failed with status: %s' % status)
            sys.exit(16)

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


CmwDeploy()
