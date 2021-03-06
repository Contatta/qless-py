'''Main qless business'''

import time
import redis
import pkgutil
import logging
import logging.handlers
import logstash_formatter
import decorator
import simplejson as json

# Internal imports
from .exceptions import QlessException

# Our logger
def _getLogger():
    ''' Set the global logger '''
    global logstash_formatter
    _logger=logging.getLogger('qless')
    if not len(_logger.handlers):
        formatter = logging.Formatter(
             '%(asctime)s | PID %(process)d | [%(levelname)s] %(message)s')
        #Log to File
        log_filename = '/var/log/qless/qless.log'
        filehandler = logging.handlers.WatchedFileHandler(log_filename)
        filehandler.setFormatter(formatter)
        filehandler.setLevel(logging.INFO)
        _logger.addHandler(filehandler)

        #logstash-readable file
        logstash_filename = '/var/log/qless/qless.json'
        stash_formatter = logstash_formatter.LogstashFormatterV1()
        logstash_handler = logging.handlers.WatchedFileHandler(logstash_filename)
        logstash_handler.setFormatter(stash_formatter)
        logstash_handler.setLevel(logging.INFO)
        _logger.addHandler(logstash_handler)

        #Log rotation
        # Log rotation adds the message into the log file redundantly (#BUG)
        # rotatehandler = logging.handlers.RotatingFileHandler(log_filename, maxBytes=104857600, backupCount=10)
        # logger.addHandler(rotatehandler)
        # logstashrotatehandler = logging.handlers.RotatingFileHandler(logstash_filename, maxBytes=104857600, backupCount=10)
        # logger.addHandler(logstashrotatehandler)

        #Log to Console
        consolehandler = logging.StreamHandler()
        consolehandler.setFormatter(formatter)
        consolehandler.setLevel(logging.DEBUG)
        _logger.addHandler(consolehandler)

    return _logger

#Set out GLOBAL logger
logger = _getLogger()

def _reloadLogger():
    global logger
    logger.info('Reloading logger configuration...')
    while len(logger.handlers) > 0:
        h = logger.handlers[0]
        logger.removeHandler(h)
    logger = _getLogger()
    logger.info('Reloaded logger configuration...')


def retry(*excepts):
    '''A decorator to specify a bunch of exceptions that should be caught
    and the job retried. It turns out this comes up with relative frequency'''
    @decorator.decorator
    def new_func(func, job):
        '''No docstring'''
        try:
            func(job)
        except tuple(excepts):
            job.retry()
    return new_func


class Jobs(object):
    '''Class for accessing jobs and job information lazily'''
    def __init__(self, client):
        self.client = client

    def complete(self, offset=0, count=25):
        '''Return the paginated jids of complete jobs'''
        return self.client('jobs', 'complete', offset, count)

    def tracked(self):
        '''Return an array of job objects that are being tracked'''
        results = json.loads(self.client('track'))
        results['jobs'] = [Job(self, **job) for job in results['jobs']]
        return results

    def tagged(self, tag, offset=0, count=25):
        '''Return the paginated jids of jobs tagged with a tag'''
        return json.loads(self.client('tag', 'get', tag, offset, count))

    def failed(self, group=None, start=0, limit=25):
        '''If no group is provided, this returns a JSON blob of the counts of
        the various types of failures known. If a type is provided, returns
        paginated job objects affected by that kind of failure.'''
        if not group:
            return json.loads(self.client('failed'))
        else:
            results = json.loads(
                self.client('failed', group, start, limit))
            results['jobs'] = self.get(*results['jobs'])
            return results

    def get(self, *jids):
        '''Return jobs objects for all the jids'''
        if jids:
            return [
                Job(self.client, **j) for j in
                json.loads(self.client('multiget', *jids))]
        return []

    def __getitem__(self, jid):
        '''Get a job object corresponding to that jid, or ``None`` if it
        doesn't exist'''
        results = self.client('get', jid)
        if not results:
            results = self.client('recur.get', jid)
            if not results:
                return None
            return RecurringJob(self.client, **json.loads(results))
        return Job(self.client, **json.loads(results))


class Workers(object):
    '''Class for accessing worker information lazily'''
    def __init__(self, clnt):
        self.client = clnt

    def __getattr__(self, attr):
        '''What workers are workers, and how many jobs are they running'''
        if attr == 'counts':
            return json.loads(self.client('workers'))
        raise AttributeError('qless.Workers has no attribute %s' % attr)

    def __getitem__(self, worker_name):
        '''Which jobs does a particular worker have running'''
        result = json.loads(
            self.client('workers', worker_name))
        result['jobs']    = result['jobs'] or []
        result['stalled'] = result['stalled'] or []
        return result


class Queues(object):
    '''Class for accessing queues lazily'''
    def __init__(self, clnt):
        self.client = clnt

    def __getattr__(self, attr):
        '''What queues are there, and how many jobs do they have running,
        waiting, scheduled, etc.'''
        if attr == 'counts':
            return json.loads(self.client('queues'))
        raise AttributeError('qless.Queues has no attribute %s' % attr)

    def __getitem__(self, queue_name):
        '''Get a queue object associated with the provided queue name'''
        return Queue(queue_name, self.client, self.client.worker_name)


class Client(object):
    '''Basic qless client object.'''
    def __init__(self, url='redis://localhost:6379', hostname=None, **kwargs):
        import socket
        # This is our unique idenitifier as a worker
        self.worker_name = hostname or socket.gethostname()
        # This is just the redis instance we're connected to conceivably
        # someone might want to work with multiple instances simultaneously.
        self.redis = redis.Redis.from_url(url, **kwargs)
        self.jobs = Jobs(self)
        self.queues = Queues(self)
        self.config = Config(self)
        self.workers = Workers(self)

        # We now have a single unified core script.
        data = pkgutil.get_data('qless', 'qless-core/qless.lua')
        self._lua = self.redis.register_script(data)

    def __getattr__(self, key):
        if key == 'events':
            self.events = Events(self.redis)
            return self.events
        raise AttributeError('%s has no attribute %s' % (
            self.__class__.__module__ + '.' + self.__class__.__name__, key))

    def __call__(self, command, *args):
        lua_args = [command, repr(time.time())]
        lua_args.extend(args)
        try:
            return self._lua(keys=[], args=lua_args)
        except redis.ResponseError as exc:
            raise QlessException(str(exc))

    def track(self, jid):
        '''Begin tracking this job'''
        return self('track', 'track', jid)

    def untrack(self, jid):
        '''Stop tracking this job'''
        return self('track', 'untrack', jid)

    def tags(self, offset=0, count=100):
        '''The most common tags among jobs'''
        return json.loads(self('tag', 'top', offset, count))

    def unfail(self, group, queue, count=500):
        '''Move jobs from the failed group to the provided queue'''
        return self('unfail', queue, group, count)

from .job import Job, RecurringJob
from .queue import Queue
from .config import Config
from .listener import Events
