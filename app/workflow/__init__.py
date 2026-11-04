# -*- coding: utf-8 -*-
"""录题自动化任务工作流。

入口：python -m workflow.runner --help
"""
from . import checks, contracts, manifest, review  # noqa: F401

__all__ = ["checks", "contracts", "manifest", "review"]
__version__ = "1.0"
