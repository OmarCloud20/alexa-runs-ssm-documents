import boto3
import logging
import os
import json
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key



'''
This is the Master Lambda function that is be called by the Lambda function hosting Alexa skill. This function
is to query the DynamoDB table for the document name and parameters based on the command name. It validates
the DynamoDB query response. If the response is valid, it returns the document name and parameters. If the response 
is invalid, it returns the error message. It also queries all instances with the tag key and value and then 
create a list for instances managed by SSM and are online. Finally, it sends commands to these instances and
returns the response to the Alexa skill.

The following environment variables are required:
1. Systems Manager IAM Service Role ARN
2. SNS Topic ARN
3. DynamoDB table name

Note:
- "Alexa" is the tag key. You can change it to whatever you want.
'''


logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2 = boto3.client('ec2')
ssm = boto3.client('ssm')
dynamodb = boto3.resource('dynamodb')

tag_key = 'Alexa'



SSM_SERVICE_ROLE = os.environ['SSM_Role_ARN']
SNS_TOPIC_ARN = os.environ['SNS_Topic_ARN']
DYNAMODB_TABLE_NAME = os.environ['DynamoDB_Table_Name']



def lambda_handler(event, context):
    logging.info(event)
    logging.info('Command: %s, and Tag: %s', event['command'], event['tag'])

    try:
        # Calling function get_document_name to return the document name and parameters for the specific command
        Items = get_document_name(event['command'])
        logging.info('Items: %s', Items)

        # Verify if Items is not empty and if it has DocumentName and Parameters
        if type(Items) is dict or type(Items) is list:
            logging.info('Items: %s', Items)
            # get Document_Name and parameters from Items
            for item in Items:
                Document_Name = item.get('DocumentName')
                parameters = item.get('Parameters')
                logging.info('DocumentName: %s, Parameters: %s', Document_Name, parameters)
                
            logging.info('Document name: %s, Parameters: %s', Document_Name, parameters)
            logging.info('%s command is running SSM document name: %s', event['command'], Document_Name)

            # Calling function to validate instance tags and return ssm managed instance ids
            managed_instance_ids, tag_filter, tag_value = validate(event)
            logging.info('Managed instance ids: %s', managed_instance_ids)

            # Send commands to managed instances if there are any
            if len(managed_instance_ids) > 0:
                logging.info('Sending command to managed instances...')
                # calling function send_command to send commands to managed instances
                return send_command(Document_Name, parameters, managed_instance_ids, tag_filter, event)

            else:
                logging.info('No instances found for the given tag')
                return   "I couldn't find any running instances tagged with {}. You can run a different command or say cancel to exit.".format(tag_value)
        else:
            # If Items is empty, return the error message
            logging.info('Items is empty')
            return Items 

    except ClientError as e:
        logging.error(e)
        return "I'm sorry, I couldn't run the command due to an error.".format(e)

# function to validate of the command and to return the document name and parameters from DynamoDB table
def get_document_name(command):

    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    try:
        response = table.query(
            KeyConditionExpression=Key('Command').eq(command)
        )
        logging.info('response: %s', response)
        
        if response['Items']:
            if 'DocumentName' in response['Items'][0] and 'Parameters' in response['Items'][0]:
                if response['Items'][0]['DocumentName'] != '' and response['Items'][0]['Parameters'] != '':
                    logging.info('Document name: %s, Parameters: %s', response['Items'][0]['DocumentName'], response['Items'][0]['Parameters'])
                    return response['Items']
                elif response['Items'][0]['DocumentName'] != '' and response['Items'][0]['Parameters'] == '':
                    logging.info('Document name: %s, Parameters: empty', response['Items'][0]['DocumentName'])
                    return response['Items']
                else:
                    logging.info('Document name and parameters are empty')
                    return 'I found {} command in the DynamoDB table but it does not have a configured document. What other command would you like to run?'.format(command)
            elif 'DocumentName' in response['Items'][0] and 'Parameters' not in response['Items'][0]:
                if response['Items'][0]['DocumentName'] != '':
                    logging.info('Document name: %s, Parameters: no parameters', response['Items'][0]['DocumentName'])
                    return response['Items']
                else:
                    logging.info('Document name exists but it is empty')
                    return 'I found {} command in the DynamoDB table but it does not have a configured document. What other command would you like to run?'.format(command)
            else:
                logging.info('Document name and parameters do not exist')
                return 'I found {} command in the DynamoDB table but it does not have a configured document. What other command would you like to run?'.format(command)

        else:
            logging.info('No items found')
            return "I couldn't find {} command in the DynamoDB table. What other command would you like to run?".format(command)

    except ClientError as e:
        logging.error('Unable to get document name from DynamoDB table due to: %s', e.response['Error']['Message'])
        return "I'm unable to get document name from DynamoDB table due to {}.".format(e.response['Error']['Message'])

def validate(event):

    '''
    Validation step:
    All instances with specified tag key and value will be listed. A new list will be created for
    online instances managed by SSM (only online). If the number of online instances managed by SSM
    is greater than 0, the command will be sent to these instances via Run Command. 
    '''

    # tag filter used by ssm commands
    tag_value = event['tag']
    tag_filter = [
        {'Key': 'tag:' + tag_key, 'Values': [tag_value]}
        ]

    # A new ec2 tag filter because it's different than ssm tag filter
    tag_filter_2 = [
        {'Name': 'tag:' + tag_key, 'Values': [tag_value]}
        ]

    # get instances with specified tag key and value
    instances = ec2.describe_instances(Filters=tag_filter_2)
    instance_ids = []
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            instance_ids.append(instance['InstanceId'])
    logging.info('Instance IDs: %s', instance_ids)

    # get online instances managed by SSM
    managed_instance_ids = get_managed_instances(instance_ids, tag_filter)
    logging.info('Managed instance IDs: %s', managed_instance_ids)
    logging.info('Number of managed instances: %s', len(managed_instance_ids))
    return  managed_instance_ids, tag_filter, tag_value

    # function to return a list of all managed instances
def get_managed_instances(instance_ids, tag_filter):
    managed_instance_ids = []
    for instance_id in instance_ids:
        for instance in ssm.describe_instance_information(Filters=tag_filter)['InstanceInformationList']:
            if instance['InstanceId'] == instance_id and instance['PingStatus'] == 'Online':
                managed_instance_ids.append(instance_id)
    logging.info('Managed instance IDs: %s', managed_instance_ids)
    return managed_instance_ids


def send_command(Document_Name, parameters, managed_instance_ids, tag_filter, event):

    try:
        # if parameters does not exist or is empty, then send command without parameters
        if parameters == "" or parameters == None:
            try:
                response = ssm.send_command(
                    Targets= tag_filter,
                    DocumentName="AWS-RunDocument",
                    DocumentVersion='$LATEST',
                    Parameters={
                        'sourceType': ['SSMDocument'],
                        'sourceInfo': ['{"name": "'+Document_Name+'"}'],
                        'documentParameters': ['{}']
                    },
                    TimeoutSeconds=3600,
                    MaxConcurrency='50',
                    MaxErrors='0',
                    Comment='Alexa - {}.'.format(Document_Name),
                    ServiceRoleArn=SSM_SERVICE_ROLE,
                    NotificationConfig={
                        'NotificationArn': SNS_TOPIC_ARN,
                        'NotificationEvents': [
                            'All',
                        ],
                        'NotificationType': 'Command'
                    }
                )
                print('Omar 1')
                print('Document_Name:', Document_Name)
                logging.info(response)
                logging.info('Command status is %s for %s instances', response['Command']['Status'], len(managed_instance_ids))
                # calling validate instance count function
                return validate_instance_count(event, managed_instance_ids, response)
            
            except ClientError as e:
                logging.error('Unable to send command due to: %s', e.response['Error']['Message'])
                return "I'm unable to send command due to {}.".format(e.response['Error']['Message'])
        else:

            documentParameters = {
                "Operation": "Install",
                "RebootOption": "RebootIfNeeded"
                }
            response = ssm.send_command(
                Targets= tag_filter,
                DocumentName="AWS-RunDocument",
                DocumentVersion='$LATEST',
                Parameters={
                    'sourceType': ['SSMDocument'],
                    'sourceInfo': ['{"name": "'+Document_Name+'"}'],
                    'documentParameters': [json.dumps(documentParameters)]
                },
                TimeoutSeconds=3600,
                MaxConcurrency='50',
                MaxErrors='0',
                Comment='Alexa - {}.'.format(Document_Name),
                ServiceRoleArn=SSM_SERVICE_ROLE,
                NotificationConfig={
                    'NotificationArn': SNS_TOPIC_ARN,
                    'NotificationEvents': [
                        'All',
                    ],
                    'NotificationType': 'Command'

                }
            )
            logging.info(response)
            logging.info('Command status is %s for %s instances', response['Command']['Status'], len(managed_instance_ids))
            # calling validate instance count function
            return validate_instance_count(event, managed_instance_ids, response)

    except Exception as e:
        logging.error("I'm sorry, I have encountered an error due to: %s", e)
        return "I'm sorry, I have encountered an error due to {}.".format(e)
    
# function to validate the number of instances found for the given tag
def validate_instance_count(event, managed_instance_ids, response):
    logging.info('Number of managed instances: %s', len(managed_instance_ids))
    if len(managed_instance_ids) == 1:
        logging.info('I have sent the command {} to {} instance tagged with {} and its current status is {}. You will receive a Slack notification when the command starts and completes.'.format(event['command'], len(managed_instance_ids), event['tag'], response['Command']['Status']))
        return "I have sent command {} to {} instance tagged with {} and its current status is {}. You will receive a Slack notification when the command starts and completes.".format(event['command'], len(managed_instance_ids), event['tag'], response['Command']['Status'])
    else:
        logging.info('I have sent the command {} to {} instances tagged with {} and their current status is {}. You will receive a Slack notification when the command starts and completes.'.format(event['command'], len(managed_instance_ids), event['tag'], response['Command']['Status']))
        return "I have sent the command {} to {} instances tagged with {} and their current status is {}. You will receive a Slack notification when the command starts and completes.".format(event['command'], len(managed_instance_ids), event['tag'], response['Command']['Status'])
