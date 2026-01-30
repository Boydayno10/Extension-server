import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Tuple

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


# TTL (em segundos) configurável via variável de ambiente.
#
# Por padrão usamos 0 para garantir que **toda** requisição consulte
# diretamente o Supabase, sem reutilizar dados em cache.
# Se quiser habilitar cache, defina EMAKUA_CACHE_TTL_SECONDS>0.
_CACHE_TTL_SECONDS: int = int(os.environ.get("EMAKUA_CACHE_TTL_SECONDS", "0"))

# Cache em memória separado por recurso: nome -> (timestamp, dados)
_resource_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _fetch_resource(name: str) -> Dict[str, Any]:
    """Busca o JSON bruto no Supabase, sem cache."""
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


def _get_with_ttl(name: str) -> Dict[str, Any]:
    """Retorna o recurso do cache em memória com TTL ou recarrega do Supabase."""

    # Se TTL <= 0, desabilita completamente o cache: sempre consulta Supabase.
    if _CACHE_TTL_SECONDS <= 0:
        return _fetch_resource(name)

    now = time.time()
    cached = _resource_cache.get(name)
    if cached is not None:
        ts, data = cached
        if now - ts < _CACHE_TTL_SECONDS:
            return data

    data = _fetch_resource(name)
    _resource_cache[name] = (now, data)
    return data


def get_pt_emakua_lexicon() -> Dict[str, Any]:
    """Retorna o léxico pt_emakua_lexicon com cache em memória e TTL."""

    raw = _get_with_ttl("pt_emakua_lexicon.json")
    if not isinstance(raw, dict):
        raise RuntimeError("Léxico inválido (metadata não é um objeto).")

    # Normalize values to List[str].
    # This keeps the translation pipeline stable even if some entries were
    # saved as a single string.
    normalized: Dict[str, Any] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            continue

        if isinstance(v, str):
            s = v.strip()
            if s:
                normalized[k] = [s]
            continue

        if isinstance(v, list):
            cleaned = [
                item.strip()
                for item in v
                if isinstance(item, str) and item.strip()
            ]
            if cleaned:
                normalized[k] = cleaned
            continue

        # Ignore other types (numbers/objects) to avoid corrupting indexes.

    return normalized


def get_emakua_grammar() -> Dict[str, Any]:
    """Retorna a gramática emakua_grammar com cache em memória e TTL."""

    return _get_with_ttl("emakua_grammar.json")


def get_emakua_pronouns() -> Dict[str, Any]:
    """Retorna os pronomes emakua_pronouns com cache em memória e TTL."""

    return _get_with_ttl("emakua_pronouns.json")


def load_resources() -> EmakuaResources:
    """Carrega todos os recursos necessários para o pipeline de tradução.

    A chamada continua sendo simples para o restante do código, mas por baixo
    cada tipo de dado é carregado com cache em memória e TTL.
    """

    grammar = get_emakua_grammar()
    pronouns = get_emakua_pronouns()
    lexicon = get_pt_emakua_lexicon()

    return EmakuaResources(grammar=grammar, pronouns=pronouns, lexicon=lexicon)
