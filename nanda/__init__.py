"""Compatibilità con lo stack NANDA (nodo di Milano).

  catalog.py      formato AI Catalog (hop 2 dell'Index v2), servito dal nostro gateway
  index_client.py client del NANDA Index v2 + risoluzione URN -> card + sorgente per la discovery
  signing.py      AgentAddr firmato Ed25519, AgentFacts come Verifiable Credential, did:web
  local_index.py  finto nodo di Milano compatibile con l'Index v2, per le prove
  town.py         schede YAML per Nanda Town 2
"""
from .catalog import catalog_document, catalog_entry
from .index_client import NandaIndexClient, NandaIndexSource, ResolvedAgent

__all__ = ["NandaIndexClient", "NandaIndexSource", "ResolvedAgent", "catalog_document", "catalog_entry"]
