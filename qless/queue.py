#! /usr/bin/env python

'''Our Queue and supporting classes'''

import time
import uuid
from qless.job import Job
import simplejson as json


class Jobs(object):
    '''A proxy object for queue-specific job information'''
    def __init__(self, name, client):
        self.name = name
        self.client = client

    def running(self, offset=0, count=25):
        '''Return all the currently-running jobs'''
        return self.client('jobs', 'running', self.name, offset, count)

    def stalled(self, offset=0, count=25):
        '''Return all the currently-stalled jobs'''
        return self.client('jobs', 'stalled', self.name, offset, count)

    def scheduled(self, offset=0, count=25):
        '''Return all the currently-scheduled jobs'''
        return self.client('jobs', 'scheduled', self.name, offset, count)

    def depends(self, offset=0, count=25):
        '''Return all the currently dependent jobs'''
        return self.client('jobs', 'depends', self.name, offset, count)

    def recurring(self, offset=0, count=25):
        '''Return all the recurring jobs'''
        return self.client('jobs', 'recurring', self.name, offset, count)


class Queue(object):
    '''The Queue class'''
    def __init__(self, name, client, worker_name):
        self.name        = name
        self.client      = client
        self.worker_name = worker_name
        self._hb         = 60

    def __getattr__(self, key):
        if key == 'jobs':
            self.jobs = Jobs(self.name, self.client)
            return self.jobs
        if key == 'counts':
            return json.loads(self.client('queues', self.name))
        if key == 'heartbeat':
            conf = self.client.config.all
            return int(conf.get(
                self.name + '-heartbeat', conf.get('heartbeat', 60)))
        raise AttributeError('qless.Queue has no attribute %s' % key)

    def __setattr__(self, key, value):
        if key == 'heartbeat':
            self.client.config[self.name + '-heartbeat'] = value
        else:
            object.__setattr__(self, key, value)

    def class_string(self, klass):
        '''Return a string representative of the class'''
        if isinstance(klass, basestring):
            return klass
        return klass.__module__ + '.' + klass.__name__

    def put(self, klass, data, priority=None, tags=None, delay=None,
        retries=5, jid=None, depends=None, replace=1, resources=None, interval=None):
        '''Either create a new job in the provided queue with the provided
        attributes, or move that job into that queue. If the job is being
        serviced by a worker, subsequent attempts by that worker to either
        `heartbeat` or `complete` the job should fail and return `false`.

        The `priority` argument should be negative to be run sooner rather
        than later, and positive if it's less important. The `tags` argument
        should be a JSON array of the tags associated with the instance and
        the `valid after` argument should be in how many seconds the instance
        should be considered actionable.'''
        return self.client('put', self.worker_name, self.name,
            jid or uuid.uuid4().hex,
            self.class_string(klass),
            json.dumps(data),
            delay or 0,
            'priority', priority or 0,
            'tags', json.dumps(tags or []),
            'retries', retries,
            'depends', json.dumps(depends or []),
            'replace', replace,
            'resources', json.dumps(resources or []),
            'interval', interval or 0.0
        )

    def recur(self, klass, data, interval, offset=0, priority=None, tags=None,
        retries=None, resources=None, jid=None,):
        '''Place a recurring job in this queue'''
        return self.client('recur', self.name,
            jid or uuid.uuid4().hex,
            self.class_string(klass),
            json.dumps(data),
            'interval', interval, offset,
            'priority', priority or 0,
            'tags', json.dumps(tags or []),
            'retries', retries or 5,
            'resources', json.dumps(resources or [])
        )

    def pop(self, count=None):
        '''Passing in the queue from which to pull items, the current time,
        when the locks for these returned items should expire, and the number
        of items to be popped off.'''
        results = [Job(self.client, **job) for job in json.loads(
            self.client('pop', self.name, self.worker_name, count or 1))]
        if count == None:
            return (len(results) and results[0]) or None
        return results

    def peek(self, count=None):
        '''Similar to the pop command, except that it merely peeks at the next
        items'''
        results = [Job(self.client, **rec) for rec in json.loads(
            self.client('peek', self.name, count or 1))]
        if count == None:
            return (len(results) and results[0]) or None
        return results

    def stats(self, date=None):
        '''Return the current statistics for a given queue on a given date.
        The results are returned are a JSON blob::

            {
                'total'    : ...,
                'mean'     : ...,
                'variance' : ...,
                'histogram': [
                    ...
                ]
            }

        The histogram's data points are at the second resolution for the first
        minute, the minute resolution for the first hour, the 15-minute
        resolution for the first day, the hour resolution for the first 3
        days, and then at the day resolution from there on out. The
        `histogram` key is a list of those values.'''
        return json.loads(
            self.client('stats', self.name, date or repr(time.time())))

    def __len__(self):
        return self.client('length', self.name)
