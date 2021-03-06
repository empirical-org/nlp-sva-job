#!/usr/bin/env python
# -*- coding: utf-8 -*-
from sentence_helper import get_sentences
import logging
import os
import pika
import io
import re
import socket
import json

FNAME=os.path.basename(__file__)
PID=os.getpid()
HOST=socket.gethostname()

# set up logging
log_filename='sentencer_{}.log'.format(os.getpid())
log_format = '%(levelname)s %(asctime)s {pid} {filename} %(lineno)d %(message)s'.format(
        pid=PID, filename=FNAME)
logging.basicConfig(format=log_format,
    filename='/var/log/sentencerlogs/{}'.format(log_filename),
    datefmt='%Y-%m-%dT%H:%M:%S%z',
    level=logging.INFO)
logger = logging.getLogger('sentencer')


try:
    JOB_NAME = os.environ['JOB_NAME']
    PRE_SENTENCES_BASE = os.environ['PRE_SENTENCES_QUEUE_BASE']
    PRE_SENTENCES_QUEUE = PRE_SENTENCES_BASE + '_' + JOB_NAME
    RABBIT = os.environ.get('RABBITMQ_LOCATION', 'localhost')
    SENTENCER_PREFETCH_COUNT = int(os.environ.get('SENTENCER_PREFETCH_COUNT', 10))
    SENTENCES_BASE = os.environ['SENTENCES_QUEUE_BASE']
    SENTENCES_QUEUE = SENTENCES_BASE + '_' + JOB_NAME
except KeyError as e:
    logger.critical("important environment variables were not set.")
    raise Exception('important environment variables were not set')

def handle_message(ch, method, properties, body):
    try:
        body = body.decode('utf-8')
        for sentence in get_sentences(body):
            channel.basic_publish(exchange='', routing_key=SENTENCES_QUEUE,
                    body=json.dumps(sentence))
        logger.info("queued sentences")
    except Exception as e:
        logger.error("problem handling message - {}".format(e))
    ch.basic_ack(delivery_tag=method.delivery_tag)


if __name__ == '__main__':
    connection = pika.BlockingConnection(pika.ConnectionParameters(RABBIT))
    channel = connection.channel()
    channel.queue_declare(queue=PRE_SENTENCES_QUEUE) # create queue if doesn't exist
    channel.queue_declare(queue=SENTENCES_QUEUE)

    # NOTE: if the prefetch count is too high, some workers could starve. If it
    # is too low, we make an unneccessary amount of requests to rabbitmq server
    channel.basic_qos(prefetch_count=SENTENCER_PREFETCH_COUNT) # limit num of unackd msgs on channel
    channel.basic_consume(handle_message, queue=PRE_SENTENCES_QUEUE, no_ack=False)
    channel.start_consuming()
