#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pathlib import Path
from setuptools import setup

version = "1.4.1"

# read the contents of your README file

long_description = (Path(__file__).parent / "README.md").read_text()

setup(
    name="structlog-sentry",
    version="1.4.1",
    description="Sentry integration for structlog",
    author="Kiwi.com platform",
    author_email="platform@kiwi.com",
    packages=["structlog_sentry"],
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="MIT",
)
