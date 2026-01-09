import os
from dataclasses import dataclass
from typing import Any, Dict

from supabase import Client, create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

TABLE_NAME = "emakua_ml_resources"

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY/ANON_KEY precisam estar definidos no ambiente.")

_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@dataclass
class EmakuaResources:
    grammar: Dict[str, Any]
    pronouns: Dict[str, Any]
    lexicon: Dict[str, Any]


_cache: EmakuaResources | None = None


def _fetch_resource(name: str) -> Dict[str, Any]:
    resp = (
        _client
        .table(TABLE_NAME)
        .select("metadata")
        .eq("name", name)
        .execute()
    )
    data = getattr(resp, "data", None)
    if not data:
        raise RuntimeError(f"Recurso {name} não encontrado na tabela {TABLE_NAME}.")
    return data[0]["metadata"]


def load_resources() -> EmakuaResources:
    global _cache
    if _cache is not None:
        return _cache

    grammar = _fetch_resource("emakua_grammar.json")
    pronouns = _fetch_resource("emakua_pronouns.json")
    lexicon = _fetch_resource("pt_emakua_lexicon.json")

    _cache = EmakuaResources(grammar=grammar, pronouns=pronouns, lexicon=lexicon)
    return _cache
