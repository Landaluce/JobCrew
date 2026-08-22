from pathlib import Path

import pytest

from job_automation.config import load_config


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_config_returns_empty_dict_when_no_files(tmp_path: Path) -> None:
    assert load_config(tmp_path) == {}


def test_load_config_reads_base_file(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "config.yaml", "search:\n  query: python developer\n")
    config = load_config(tmp_path)
    assert config == {"search": {"query": "python developer"}}


def test_load_config_local_overrides_base_shallow_key(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "config.yaml", "search:\n  query: base\n  location: Remote\n")
    _write_yaml(tmp_path / "config.local.yaml", "search:\n  query: local override\n")
    config = load_config(tmp_path)
    assert config["search"]["query"] == "local override"
    assert config["search"]["location"] == "Remote"


def test_load_config_deep_merge_nested_dicts(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path / "config.yaml",
        "application:\n  max_applications: 3\n  auto_submit: false\nllm:\n  model: llama3.2:3b\n",
    )
    _write_yaml(
        tmp_path / "config.local.yaml",
        "application:\n  max_applications: 5\n",
    )
    config = load_config(tmp_path)
    assert config["application"] == {"max_applications": 5, "auto_submit": False}
    assert config["llm"]["model"] == "llama3.2:3b"


def test_load_config_treats_empty_files_as_empty(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "config.yaml", "")
    _write_yaml(tmp_path / "config.local.yaml", "")
    assert load_config(tmp_path) == {}
