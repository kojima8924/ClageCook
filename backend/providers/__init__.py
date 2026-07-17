# -*- coding: utf-8 -*-
"""AIプロバイダ群。将来 anthropic/openai/gemini/xai を追加する。"""
from .base import Provider
from .mock import MockProvider

__all__ = ["Provider", "MockProvider"]
