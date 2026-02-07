"""
title: Anthropic Claude API
author: carlosaln (adapted for equity research)
author_url: https://github.com/carlosaln/open-webui-functions
version: 0.3.0
license: MIT
description: Connect Open WebUI to Claude API (Opus 4.6, Sonnet 4.5, etc.)
"""

import os
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field, field_validator
from open_webui.utils.misc import pop_system_message

logger = logging.getLogger(__name__)


class ContentType(Enum):
    TEXT = "text"
    IMAGE = "image"
    THINKING = "thinking"


class EventType(Enum):
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    MESSAGE = "message"
    MESSAGE_STOP = "message_stop"


class DeltaType(Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"


class ThinkingState(Enum):
    NOT_STARTED = -1
    IN_PROGRESS = 0
    COMPLETED = 1


class ImageSourceType(Enum):
    BASE64 = "base64"
    URL = "url"


@dataclass
class ModelConfig:
    id: str
    name: str
    api_identifier: str = field(default="")

    def __post_init__(self):
        if not self.api_identifier:
            self.api_identifier = self.id


class Pipe:
    class Valves(BaseModel):
        ANTHROPIC_API_KEY: str = Field(default="")
        LOG_LEVEL: str = Field(default="WARNING")

        @field_validator("ANTHROPIC_API_KEY")
        def check_api_key(cls, value: str) -> str:
            if not value:
                logger.warning("ANTHROPIC_API_KEY is not set")
            return value

        @field_validator("LOG_LEVEL")
        def check_log_level(cls, value: str) -> str:
            valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if value.upper() not in valid_levels:
                return "WARNING"
            return value.upper()

    API_VERSION = "2023-06-01"
    API_ENDPOINT = "https://api.anthropic.com/v1/messages"
    MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
    MAX_TOTAL_IMAGE_SIZE_BYTES = 100 * 1024 * 1024
    DEFAULT_TIMEOUT = (3.05, 60)

    def __init__(self):
        self.type = "manifold"
        self.id = "anthropic"
        self.name = "anthropic/"
        self.valves = self.Valves(
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", ""),
            LOG_LEVEL=os.getenv("ANTHROPIC_LOG_LEVEL", "WARNING"),
        )
        log_level = getattr(logging, self.valves.LOG_LEVEL)
        logger.setLevel(log_level)
        self._models = self._initialize_models()

    def _initialize_models(self) -> List[ModelConfig]:
        return [
            ModelConfig(id="claude-opus-4-20250514", name="Claude Opus 4"),
            ModelConfig(id="claude-sonnet-4-20250514", name="Claude Sonnet 4"),
            ModelConfig(id="claude-3-5-sonnet-20241022", name="Claude 3.5 Sonnet"),
            ModelConfig(id="claude-3-7-sonnet-latest", name="Claude 3.7 Sonnet"),
            ModelConfig(id="claude-3-5-haiku-20241022", name="Claude 3.5 Haiku"),
        ]

    @lru_cache(maxsize=1)
    def get_anthropic_models(self) -> List[Dict[str, str]]:
        return [{"id": model.id, "name": model.name} for model in self._models]

    def pipes(self) -> List[Dict[str, str]]:
        return self.get_anthropic_models()

    def process_image(self, image_data: Dict[str, Any]) -> Dict[str, Any]:
        image_url = image_data["image_url"]["url"]
        if image_url.startswith("data:image"):
            mime_type, base64_data = image_url.split(",", 1)
            media_type = mime_type.split(":")[1].split(";")[0]
            image_size = len(base64_data) * 3 / 4
            if image_size > self.MAX_IMAGE_SIZE_BYTES:
                raise ValueError(f"Image size exceeds 5MB limit: {image_size / (1024 * 1024):.2f}MB")
            return {
                "type": ContentType.IMAGE.value,
                "source": {"type": ImageSourceType.BASE64.value, "media_type": media_type, "data": base64_data},
            }
        else:
            parsed_url = urlparse(image_url)
            if not (parsed_url.scheme and parsed_url.netloc):
                raise ValueError(f"Invalid image URL: {image_url}")
            return {
                "type": ContentType.IMAGE.value,
                "source": {"type": ImageSourceType.URL.value, "url": image_url},
            }

    def _select_model(self, requested_model_id: str) -> Tuple[str, bool]:
        model_short_name = requested_model_id.split("/", 1)[-1] if "/" in requested_model_id else requested_model_id
        if "." in model_short_name:
            model_short_name = model_short_name.split(".", 1)[-1]
        extended_thinking = "-extended-thinking" in model_short_name.lower()
        for model in self._models:
            if model.id == model_short_name:
                return model.api_identifier, extended_thinking
        logger.warning(f"Unknown model: {requested_model_id}, defaulting to claude-opus-4-20250514")
        return "claude-opus-4-20250514", False

    def _process_messages(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        processed_messages = []
        total_image_size = 0
        for message in messages:
            content = message.get("content")
            processed_content = []
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == ContentType.TEXT.value:
                        if item.get("text", "").strip():
                            processed_content.append({"type": ContentType.TEXT.value, "text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        processed_image = self.process_image(item)
                        processed_content.append(processed_image)
                        if processed_image["source"]["type"] == ImageSourceType.BASE64.value:
                            image_size = len(processed_image["source"]["data"]) * 3 / 4
                            total_image_size += image_size
                            if total_image_size > self.MAX_TOTAL_IMAGE_SIZE_BYTES:
                                raise ValueError("Total image size exceeds 100MB limit")
            else:
                processed_content = [{"type": ContentType.TEXT.value, "text": str(content)}]
            processed_messages.append({"role": message["role"], "content": processed_content})
        return processed_messages, total_image_size

    def _prepare_payload(self, body, processed_messages, chosen_model, extended_thinking, system_message):
        payload = {"model": chosen_model, "messages": processed_messages, "stream": body.get("stream", False)}
        if "stop" in body:
            payload["stop_sequences"] = body["stop"]
        if extended_thinking:
            max_tokens = body.get("max_tokens", 20000)
            budget_tokens = min(16000, max_tokens - 1)
            payload["max_tokens"] = max_tokens
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
            for param in ["temperature", "top_k", "top_p"]:
                if param in body:
                    payload[param] = body[param]
        else:
            for param, default in [("max_tokens", 4096), ("temperature", 0.8), ("top_k", 40), ("top_p", 0.9)]:
                payload[param] = body.get(param, default)
        if system_message:
            payload["system"] = str(system_message)
        return payload

    def pipe(self, body: Dict[str, Any]) -> Union[str, Generator[str, None, None]]:
        if not self.valves.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required. Set it in the Valves configuration.")
        if "model" not in body:
            raise ValueError("Model must be specified")
        if "messages" not in body:
            raise ValueError("Messages must be specified")

        system_message, messages = pop_system_message(body["messages"])
        processed_messages, _ = self._process_messages(messages)
        chosen_model, extended_thinking = self._select_model(body["model"])

        headers = {
            "x-api-key": self.valves.ANTHROPIC_API_KEY,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        payload = self._prepare_payload(body, processed_messages, chosen_model, extended_thinking, system_message)

        try:
            if body.get("stream", False):
                return self.stream_response(self.API_ENDPOINT, headers, payload)
            else:
                return self.non_stream_response(self.API_ENDPOINT, headers, payload)
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            error_message = f"Request failed: {e}"
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_data = e.response.json()
                    if "error" in error_data:
                        error_message = f"Anthropic API Error: {error_data['error'].get('message', str(e))}"
                except:
                    error_message = f"Anthropic API Error ({e.response.status_code}): {e.response.text}"
            if body.get("stream", False):
                def error_gen():
                    yield f"Error: {error_message}"
                return error_gen()
            return f"Error: {error_message}"
        except Exception as e:
            logger.error(f"Error in pipe: {e}")
            if body.get("stream", False):
                def error_gen():
                    yield f"Error: {str(e)}"
                return error_gen()
            return f"Error: {str(e)}"

    def stream_response(self, url, headers, payload) -> Generator[str, None, None]:
        thinking_state = ThinkingState.NOT_STARTED
        try:
            with requests.post(url, headers=headers, json=payload, stream=True, timeout=self.DEFAULT_TIMEOUT) as response:
                if response.status_code != 200:
                    error_message = response.text
                    try:
                        error_data = response.json()
                        if "error" in error_data:
                            error_message = error_data["error"].get("message", response.text)
                    except:
                        pass
                    yield f"Error: Anthropic API {response.status_code}: {error_message}"
                    return
                for line in response.iter_lines():
                    if not line:
                        continue
                    decoded_line = line.decode("utf-8")
                    if not decoded_line.startswith("data: "):
                        continue
                    raw_json = decoded_line[6:]
                    if raw_json.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(raw_json)
                    except json.JSONDecodeError:
                        continue
                    event_type = data.get("type")
                    if event_type == EventType.CONTENT_BLOCK_START.value:
                        content_block = data.get("content_block", {})
                        if content_block.get("type") == ContentType.TEXT.value:
                            if thinking_state == ThinkingState.IN_PROGRESS:
                                yield "</think>\n"
                                thinking_state = ThinkingState.COMPLETED
                            yield content_block.get("text", "")
                    elif event_type == EventType.CONTENT_BLOCK_DELTA.value:
                        delta = data.get("delta", {})
                        delta_type = delta.get("type")
                        if delta_type == DeltaType.THINKING_DELTA.value:
                            if thinking_state == ThinkingState.NOT_STARTED:
                                yield "<think>"
                                thinking_state = ThinkingState.IN_PROGRESS
                            yield delta.get("thinking", "")
                        elif delta_type == DeltaType.TEXT_DELTA.value:
                            if thinking_state == ThinkingState.IN_PROGRESS:
                                yield "</think>\n"
                                thinking_state = ThinkingState.COMPLETED
                            yield delta.get("text", "")
                    elif event_type == EventType.MESSAGE.value:
                        for content in data.get("content", []):
                            ct = content.get("type")
                            if ct == ContentType.THINKING.value:
                                if thinking_state == ThinkingState.NOT_STARTED:
                                    yield "<think>"
                                    thinking_state = ThinkingState.IN_PROGRESS
                                yield content.get("text", "")
                            elif ct == ContentType.TEXT.value:
                                if thinking_state == ThinkingState.IN_PROGRESS:
                                    yield "</think>\n"
                                    thinking_state = ThinkingState.COMPLETED
                                yield content.get("text", "")
                    elif event_type == EventType.MESSAGE_STOP.value:
                        if thinking_state == ThinkingState.IN_PROGRESS:
                            yield "</think>"
                        break
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"Error: {str(e)}"

    def non_stream_response(self, url, headers, payload) -> str:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.DEFAULT_TIMEOUT)
            if response.status_code != 200:
                error_message = response.text
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_message = error_data["error"].get("message", response.text)
                except:
                    pass
                return f"Error: Anthropic API {response.status_code}: {error_message}"
            res = response.json()
            if isinstance(res.get("content"), list):
                text_parts, thinking_parts = [], []
                for item in res["content"]:
                    if item.get("type") == ContentType.TEXT.value:
                        if item.get("text", "").strip():
                            text_parts.append(item["text"])
                    elif item.get("type") == ContentType.THINKING.value:
                        thinking_parts.append(item.get("text", ""))
                if thinking_parts:
                    return f"<think>{''.join(thinking_parts)}</think>\n{''.join(text_parts)}"
                elif text_parts:
                    return "".join(text_parts)
            return str(res)
        except Exception as e:
            logger.error(f"Non-stream error: {e}")
            return f"Error: {str(e)}"
