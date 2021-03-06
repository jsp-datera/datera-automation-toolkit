#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (unicode_literals, print_function, absolute_import,
                        division)

import sys
import threading
import time
import Queue
from time import sleep
from itertools import izip_longest

import logging

from qalib.qabase.formatters import human_readable_time_from_seconds as hrts


class Parallel(object):

    """
    A helper class that makes it simpler to run tasks multiple times
    in parallel.  If you have multiple tasks you want to run in parallel
    you need to encapsulate them in a single function that accepts a variety
    of arguments.
    """

    def __init__(self, funcs, args_list=None, kwargs_list=None, max_workers=5,
                 timeout=3600, run_over_exceptions=False,
                 i_know_what_im_doing=False, output_interval=None):
        """

        :param funcs: A list of functions to be used by the workers
        :param args_list: A list of tuples of arguments required by each
                          function in `funcs`
        :param kwargs_list: A list of dictionaries of kwargs accepted
                            by each function in `funcs`
        :param max_workers: The maximum number of simultaneous threads
        :param run_over_exceptions: Will ignore given exceptions and just keep
            running threads. Don't use this. If you do use it, I will not be
            responsible for the monster/monsters you create.
        :param output_interval: Int,
            the interval at which status will be output.
        """
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())
        self.funcs = funcs
        self.args_list = args_list if args_list else []
        self.kwargs_list = kwargs_list if kwargs_list else []
        self.max_workers = max_workers
        self.queue = Queue.Queue()
        self.exceptions = Queue.Queue()
        self.threads = []
        self.timeout = timeout
        self.keep_running = True
        self.run_over_exceptions = run_over_exceptions and i_know_what_im_doing
        self.output_interval = output_interval

    @staticmethod
    def _set_current_thread_name_from_func_name(func):
        """ Renames the current thread to reflect the name of func """
        orig_thread_number = threading.current_thread().name.split('-')[-1]
        threading.current_thread().name = "Parallel-" + \
            func.__module__ + '.' + func.__name__ + "-" + orig_thread_number

    def _wrapped(self):
        threading.current_thread().name = "Parallel-Worker-" + \
            threading.current_thread().name
        while self.keep_running:
            try:
                func, args, kwargs = self.queue.get(block=False)
            except Queue.Empty:
                break
            self.logger.debug(
                "Running {} with args: {} and kwargs {} with thread {}".format(
                    func, args, kwargs, threading.current_thread()))
            try:
                # Rename this thread to reflect the function we're running
                orig_name = threading.current_thread().name
                self._set_current_thread_name_from_func_name(func)
                # Call the function:
                func(*args, **kwargs)
                # Reset this thread name to its original (e.g. "Thread-9")
                threading.current_thread().name = orig_name
            except Exception:
                self.keep_running = self.run_over_exceptions
                self.logger.exception("Exception occurred in thread {}".format(
                    threading.current_thread()))
                self.exceptions.put(sys.exc_info())

            self.queue.task_done()

    def run_threads(self):
        """
        Call this function to start the worker threads.  They will continue
        running until all args/kwargs are consumed.  This is a blocking call.
        """
        try:
            for func, args, kwargs in izip_longest(
                    self.funcs, self.args_list, self.kwargs_list,
                    fillvalue={}):
                # Flag a common (and confusing) user error:
                if isinstance(args, str) or isinstance(args, unicode):
                    msg = "args_list must be list of lists not list of strings"
                    raise ValueError(msg)
                self.queue.put((func, args, kwargs))

            for _ in xrange(self.max_workers):
                thread = threading.Thread(target=self._wrapped)
                thread.setDaemon(True)
                thread.start()
                self.threads.append(thread)

            if (len(self.funcs) < len(self.args_list) or
                    len(self.funcs) < len(self.kwargs_list)):
                raise ValueError(
                    "List of functions passed into a Parallel object must "
                    "be longer or equal in length to the list of args "
                    "and/or kwargs passed to the object.  {}, {}, {"
                    "}".format(self.funcs, self.args_list, self.kwargs_list))

            start_time = time.time()
            last_output_time = start_time
            while self.queue.unfinished_tasks:
                # Check if exception has been generated by a thread and raise
                # if found one is found
                try:
                    exc = self.exceptions.get(block=False)
                    self.keep_running = self.run_over_exceptions
                    raise exc[0], exc[1], exc[2]
                except Queue.Empty:
                    pass
                if self.output_interval is not None:
                    if time.time() - last_output_time > self.output_interval:
                        msg = ("After {} {} functions pending execution of"
                               " {} total".format(
                                hrts(time.time() - start_time),
                                self.queue.qsize(),
                                len(self.funcs)
                                ))
                        self.logger.info(msg)
                        last_output_time = time.time()
                sleep(0.2)

        # Ensure all threads will exit regardless of the current
        # state of the main thread
        finally:
            try:
                exc = self.exceptions.get(block=False)
                self.keep_running = self.run_over_exceptions
                # Join all threads to ensure we don't continue
                # without all threads stopping
                for thread in self.threads:
                    thread.join(self.timeout)
                raise exc[0], exc[1], exc[2]
            except Queue.Empty:
                pass
