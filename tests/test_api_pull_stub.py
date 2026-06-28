import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import api_pull, adapters
from connectors.contract import SourceConfig, DiscoveredExport


def _cfg(t):
    return SourceConfig("s", t, "s", "loc", "demo", "manual")


def test_api_pull_sources_are_stubbed():
    for cls, t in ((api_pull.AwsCostExplorerSource, "aws-api-pull"),
                   (api_pull.AzureExportSource, "azure-api-pull")):
        src = cls()
        assert src.source_type == t
        with pytest.raises(NotImplementedError):
            src.discover(_cfg(t))
        with pytest.raises(NotImplementedError):
            src.normalize(_cfg(t), DiscoveredExport(source_id="s", export_id="e", uri="u"))


def test_api_pull_types_marked_and_registered():
    assert api_pull.API_PULL_TYPES == {"aws-api-pull", "azure-api-pull"}
    for t in api_pull.API_PULL_TYPES:
        assert t in adapters.ADAPTERS
