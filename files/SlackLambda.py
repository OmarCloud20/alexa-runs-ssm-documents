import boto3
import json
import logging
from datetime import datetime, timedelta
import requests



"""
This lambda is to receive a payload from SSMCommandNotifications topic. The payload will be parsed and send
to Slack. The Slack webhook URL is stored in AWS Secrets Manager and is retrieved from the secret. The 
Slack channel is defined in the lambda environment variable SLACK_CHANNEL.

Omar A Omar
"""


SLACK_HOOK_URL = boto3.client('secretsmanager').get_secret_value(SecretId='SlackWebhookURL')['SecretString']
SLACK_CHANNEL = 'notifications'


# get the AWS account number to use in the slack message
account_number = boto3.client('sts').get_caller_identity().get('Account')
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    print(event)
    print("Received event: " + json.dumps(event))
    logger.info("Event: " + str(event))
    message = json.loads(event['Records'][0]['Sns']['Message'])
    subject = json.dumps(event['Records'][0]['Sns']['Subject'])
    logger.info("Message: " + str(message))
    logger.info("Subject: " + subject)

    document_name, status, event_ = message['documentName'], message['status'], message['eventTime']

    eventTime = datetime.strptime(event_, "%Y-%m-%dT%H:%M:%S.%fZ") - timedelta(hours=6)
    event_time, event_date = datetime.strftime(eventTime, "%H:%M"), datetime.strftime(eventTime, "%m-%d-%Y") 

    """ 
    Defining the color of the left side of the Slack message based on status
    """
    if status == "InProgress":
        color = "warning"
    elif status == "Success":
        color = "good"
    else:
        color = "danger"
 
    # for footer_icon, you can use your own logo or icon. It must be a public URL.
    slack_message =    {
        "channel": SLACK_CHANNEL,
        "attachments": [
            {
    	        "mrkdwn_in": ["text"],
                "color": color,
                "pretext": f"*Alexa - My Commands:* \n",
                "title": subject,
                # "text": "",
                "fields": [
                    {
                        "title": "Status",
                        "value": status,
                        "short": "false"
                    },
                    {
                        "title": "Date and Time",
                        "value": f"{event_date} {event_time}",
                        "short": "true"
                    },
                    {
                        "title": "Document Name",
                        "value": document_name,
                        "short": "true"
                    }
                ],
                'footer': 'Command Control',
                'footer_icon': 'https://platform.slack-edge.com/img/default_application_icon.png',
                'ts':  convert_time_to_ts(get_current_central_timestamp())
            }
        ]
    }
        
    try:
        response = requests.post(SLACK_HOOK_URL, data=json.dumps(slack_message), headers={'Content-Type': 'application/json'})
        logger.info('Response: %s', response)
    except Exception as e:
        logger.error('Error: %s', e)
        raise e
        
    return {
        'statusCode': 200,
        'body': json.dumps('Message sent to Slack')
    }
     
# get the current time and format it
def get_current_central_timestamp():
    now = datetime.now()
    return now.strftime("%H:%M:%S %m-%d-%Y")

# convert time to timestamp
def convert_time_to_ts(get_current_central_timestamp):
    ts = datetime.strptime(get_current_central_timestamp, "%H:%M:%S %m-%d-%Y").timestamp()
    return ts
        
        
