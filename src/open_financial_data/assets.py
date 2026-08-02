"""Provider identifier resolution into stable OFD asset identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import pyarrow.dataset as ds

if TYPE_CHECKING:
    from .project import Project


class IdentifierResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class AssetIdentity:
    country: str
    mic: str
    local_id: str

    @property
    def asset_id(self) -> str:
        return f"{self.country}.{self.mic}.{self.local_id}"


class IdentifierResolver:
    """Central deterministic mappings; persistence can later supply historical intervals."""

    _CN_SUFFIX_TO_MIC = {"SH": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}
    _MIC_TO_CN_SUFFIX = {value: key for key, value in _CN_SUFFIX_TO_MIC.items()}

    def from_provider(self, provider: str, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if provider == "tushare":
            try:
                code, suffix = normalized.split(".", 1)
                mic = self._CN_SUFFIX_TO_MIC[suffix]
            except (ValueError, KeyError) as error:
                raise IdentifierResolutionError(f"Unsupported TuShare symbol: {symbol}") from error
            return AssetIdentity("CN", mic, code.zfill(6)).asset_id
        if provider == "akshare":
            code = normalized.zfill(6)
            if not code.isdigit() or len(code) != 6:
                raise IdentifierResolutionError(f"Unsupported AKShare symbol: {symbol}")
            mic = "XBSE" if code.startswith(("4", "8")) else (
                "XSHG" if code.startswith(("5", "6", "9")) else "XSHE"
            )
            return AssetIdentity("CN", mic, code).asset_id
        raise IdentifierResolutionError(f"No identifier resolver for Provider: {provider}")

    def to_provider(self, provider: str, asset_id: str) -> str:
        try:
            country, mic, local_id = asset_id.split(".", 2)
        except ValueError as error:
            raise IdentifierResolutionError(f"Invalid asset_id: {asset_id}") from error
        if country != "CN" or mic not in self._MIC_TO_CN_SUFFIX:
            raise IdentifierResolutionError(f"Unsupported Provider asset_id: {asset_id}")
        if provider == "akshare":
            return local_id
        if provider == "akshare.sina":
            # 920xxx is BSE despite the legacy XSHG classification in
            # from_provider; Sina requires the bj prefix for it.
            prefix = "bj" if local_id.startswith("920") else self._MIC_TO_CN_SUFFIX[mic].lower()
            return f"{prefix}{local_id}"
        if provider == "tushare":
            return f"{local_id}.{self._MIC_TO_CN_SUFFIX[mic]}"
        raise IdentifierResolutionError(f"No identifier resolver for Provider: {provider}")


builtin_identifier_resolver = IdentifierResolver()


class LocalIdentifierResolver:
    """Resolve historical identifiers from the canonical reference Dataset."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def resolve(
        self,
        *,
        provider: str,
        identifier_type: str,
        identifier_value: str,
        as_of: date,
    ) -> str:
        root = self.project.root / "data" / "canonical" / "reference.asset.identifier"
        files = sorted(root.rglob("*.parquet")) if root.exists() else []
        if not files:
            raise IdentifierResolutionError("reference.asset.identifier is unavailable")
        table = ds.dataset([str(path) for path in files], format="parquet").to_table(
            columns=[
                "asset_id", "identifier_provider", "identifier_type", "identifier_value",
                "valid_from", "valid_to",
            ],
            filter=(ds.field("identifier_provider") == provider)
            & (ds.field("identifier_type") == identifier_type)
            & (ds.field("identifier_value") == identifier_value),
        )
        matches = [
            row for row in table.to_pylist()
            if row["valid_from"] <= as_of
            and (row["valid_to"] is None or as_of <= row["valid_to"])
        ]
        if len(matches) != 1:
            raise IdentifierResolutionError(
                f"Identifier resolution expected one match, found {len(matches)}"
            )
        return str(matches[0]["asset_id"])
