#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streaming.py
============

Lock-free-ish plumbing that carries samples from GNU Radio's vector sinks
into the numpy world the dashboard plots from.

``RingBuffer``  - fixed-size circular buffer of complex samples.
``ReaderThread`` - drains a ``blocks.vector_sink_c`` and pushes into a RingBuffer.

This module has no Qt or GNU Radio dependencies beyond numpy, so it is
trivial to unit-test or reuse.
"""

import threading
import time

import numpy as np


class RingBuffer:
    """Fixed-size circular buffer of complex samples, guarded by a lock.

    ``push`` appends new samples (overwriting oldest when full); ``read``
    returns a contiguous, time-ordered copy of the whole buffer.
    """

    def __init__(self, size=4096, dtype=np.complex64):
        self.size   = size
        self.dtype  = dtype
        self.buffer = np.zeros(size, dtype=dtype)
        self.index  = 0
        self.lock   = threading.Lock()

    def push(self, samples):
        with self.lock:
            n = len(samples)
            if n >= self.size:
                self.buffer[:] = samples[-self.size:]
                self.index = 0
            else:
                end = self.index + n
                if end < self.size:
                    self.buffer[self.index:end] = samples
                else:
                    first = self.size - self.index
                    self.buffer[self.index:] = samples[:first]
                    self.buffer[:n - first] = samples[first:]
                self.index = (self.index + n) % self.size

    def read(self):
        with self.lock:
            idx = self.index
            return np.concatenate((self.buffer[idx:], self.buffer[:idx]))


class ReaderThread(threading.Thread):
    """Background thread: drain a GR vector sink into a RingBuffer.

    Polls ``vector_sink.data()``, resets the sink, and pushes the samples
    into ``ringbuffer``.  Sleeps briefly when no data is available.
    """

    def __init__(self, vector_sink, ringbuffer, chunk=4096):
        super().__init__(daemon=True)
        self.vector_sink = vector_sink
        self.ringbuffer  = ringbuffer
        self.chunk       = chunk
        self.running     = True

    def run(self):
        while self.running:
            try:
                raw = self.vector_sink.data()
                if len(raw) > 0:
                    data = np.array(raw, dtype=self.ringbuffer.dtype)
                    self.vector_sink.reset()
                    self.ringbuffer.push(data)
                else:
                    time.sleep(0.001)
            except Exception:
                time.sleep(0.001)

    def stop(self):
        self.running = False
