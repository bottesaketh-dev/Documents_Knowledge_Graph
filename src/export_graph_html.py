"""
export_graph_html.py - Export Neo4j Knowledge Graph to a standalone interactive HTML visualization.

Connects to Neo4j, retrieves all nodes and relationships, and produces an interactive,
self-contained HTML dashboard with vis.js network, search, category filters, neighborhood
highlighting, and an entity inspector panel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from neo4j import GraphDatabase

# Node type styling configurations
TYPE_STYLES = {
    "Person (Family)": {"color": "#f59e0b", "shape": "dot", "size": 26, "border": "#b45309"},
    "Person": {"color": "#3b82f6", "shape": "dot", "size": 22, "border": "#1d4ed8"},
    "Document": {"color": "#06b6d4", "shape": "diamond", "size": 24, "border": "#0891b2"},
    "Organization": {"color": "#f97316", "shape": "dot", "size": 20, "border": "#ea580c"},
    "Category": {"color": "#a855f7", "shape": "square", "size": 18, "border": "#7e22ce"},
    "Asset": {"color": "#10b981", "shape": "dot", "size": 20, "border": "#059669"},
    "InsurancePolicy": {"color": "#6366f1", "shape": "dot", "size": 20, "border": "#4338ca"},
    "MedicalCondition": {"color": "#ef4444", "shape": "triangle", "size": 18, "border": "#b91c1c"},
    "Procedure": {"color": "#ec4899", "shape": "triangle", "size": 18, "border": "#be185d"},
    "Medication": {"color": "#f43f5e", "shape": "triangle", "size": 18, "border": "#be123c"},
    "Location": {"color": "#14b8a6", "shape": "dot", "size": 18, "border": "#0f766e"},
    "Event": {"color": "#eab308", "shape": "star", "size": 20, "border": "#a16207"},
    "Product": {"color": "#0284c7", "shape": "dot", "size": 18, "border": "#0369a1"},
    "Service": {"color": "#0ea5e9", "shape": "dot", "size": 18, "border": "#0284c7"},
    "Qualification": {"color": "#8b5cf6", "shape": "dot", "size": 18, "border": "#6d28d9"},
    "Record": {"color": "#64748b", "shape": "box", "size": 16, "border": "#475569"},
    "Chunk": {"color": "#94a3b8", "shape": "dot", "size": 10, "border": "#64748b"},
    "Default": {"color": "#94a3b8", "shape": "dot", "size": 16, "border": "#64748b"},
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Knowledge Graph Visualization - __TITLE__</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {
      background-color: #0b1120;
      color: #f1f5f9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow: hidden;
    }
    #network {
      width: 100%;
      height: calc(100vh - 64px);
      background-color: #0b1120;
    }
    .panel-glass {
      background: rgba(15, 23, 42, 0.88);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(51, 65, 85, 0.7);
    }
    /* Custom Scrollbar */
    ::-webkit-scrollbar {
      width: 6px;
      height: 6px;
    }
    ::-webkit-scrollbar-track {
      background: #0f172a;
    }
    ::-webkit-scrollbar-thumb {
      background: #334155;
      border-radius: 3px;
    }
  </style>
</head>
<body class="h-screen w-screen flex flex-col">

  <!-- Top Navigation Bar -->
  <header class="h-16 bg-slate-900/90 border-b border-slate-800 px-4 flex items-center justify-between z-20">
    <div class="flex items-center gap-3">
      <div class="h-9 w-9 rounded-lg bg-cyan-600/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 font-bold text-lg">
        G
      </div>
      <div>
        <h1 class="text-base font-bold text-white flex items-center gap-2">
          __TITLE__
          <span class="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">Neo4j Export</span>
        </h1>
        <p class="text-xs text-slate-400" id="stats-banner">Loading graph data...</p>
      </div>
    </div>

    <!-- Quick Search & Actions -->
    <div class="flex items-center gap-3">
      <div class="relative">
        <input 
          id="search-input" 
          type="text" 
          placeholder="Search entities, docs, people..." 
          class="w-64 bg-slate-800/80 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
        />
        <button id="search-clear" class="hidden absolute right-2.5 top-2 text-slate-400 hover:text-white text-xs">✕</button>
      </div>
      <button id="btn-fit" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition">
        Reset View
      </button>
      <button id="btn-physics" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition">
        Freeze Physics
      </button>
      <button id="btn-toggle-filters" class="px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-medium rounded-lg transition">
        Filters & Legend
      </button>
    </div>
  </header>

  <!-- Main View Container -->
  <div class="flex-1 relative overflow-hidden">
    <!-- Network Graph Canvas -->
    <div id="network"></div>

    <!-- Floating Filter / Legend Panel -->
    <aside id="filter-panel" class="panel-glass absolute top-4 left-4 w-72 rounded-xl p-4 shadow-2xl z-10 max-h-[85vh] flex flex-col gap-3 overflow-hidden transition-all duration-200">
      <div class="flex items-center justify-between pb-2 border-b border-slate-800">
        <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">Filter Node Types</h2>
        <span class="text-xs text-cyan-400 cursor-pointer hover:underline" id="toggle-all-types">Select All</span>
      </div>
      <div id="type-filters" class="flex-1 overflow-y-auto pr-1 flex flex-col gap-1.5 text-xs">
        <!-- Rendered dynamically -->
      </div>
      <div class="pt-2 border-t border-slate-800">
        <label class="flex items-center gap-2 cursor-pointer text-xs text-slate-300">
          <input type="checkbox" id="show-chunks-checkbox" class="rounded bg-slate-800 border-slate-700 text-cyan-500 focus:ring-0">
          <span>Include Text Chunks (<span id="chunk-count">0</span>)</span>
        </label>
      </div>
    </aside>

    <!-- Node Inspector Sidebar (Slides from Right) -->
    <aside id="inspector-panel" class="panel-glass absolute top-0 right-0 h-full w-96 p-5 shadow-2xl z-10 flex flex-col gap-4 translate-x-full transition-transform duration-300 ease-in-out overflow-y-auto">
      <div class="flex items-start justify-between border-b border-slate-800 pb-3">
        <div>
          <span id="insp-badge" class="px-2 py-0.5 text-xs font-bold rounded-full bg-cyan-900/60 text-cyan-300 border border-cyan-700">Type</span>
          <h3 id="insp-title" class="text-base font-bold text-white mt-1 break-words">Node Name</h3>
        </div>
        <button id="insp-close" class="text-slate-400 hover:text-white p-1 text-sm">✕</button>
      </div>

      <div class="flex flex-col gap-3 text-xs">
        <div>
          <h4 class="text-slate-400 font-semibold mb-1 uppercase tracking-wider text-[10px]">Properties</h4>
          <div id="insp-props" class="bg-slate-900/80 rounded-lg p-3 border border-slate-800 flex flex-col gap-1.5 overflow-x-auto">
            <!-- Properties rendered here -->
          </div>
        </div>

        <div>
          <h4 class="text-slate-400 font-semibold mb-1 uppercase tracking-wider text-[10px]">Connected Relationships (<span id="insp-rel-count">0</span>)</h4>
          <div id="insp-rels" class="flex flex-col gap-1.5 max-h-72 overflow-y-auto pr-1">
            <!-- Relationships rendered here -->
          </div>
        </div>
      </div>
    </aside>
  </div>

  <!-- Embedded Raw Graph JSON Payload -->
  <script>
    const RAW_GRAPH = __GRAPH_DATA_JSON__;
  </script>

  <!-- Network Initialization and Interaction Logic -->
  <script>
    let network = null;
    let nodesDataSet = null;
    let edgesDataSet = null;
    let physicsEnabled = true;
    let activeTypeFilters = new Set();
    let showChunks = false;

    // Elements
    const container = document.getElementById('network');
    const statsBanner = document.getElementById('stats-banner');
    const searchInput = document.getElementById('search-input');
    const searchClear = document.getElementById('search-clear');
    const btnFit = document.getElementById('btn-fit');
    const btnPhysics = document.getElementById('btn-physics');
    const btnToggleFilters = document.getElementById('btn-toggle-filters');
    const filterPanel = document.getElementById('filter-panel');
    const inspectorPanel = document.getElementById('inspector-panel');
    const inspBadge = document.getElementById('insp-badge');
    const inspTitle = document.getElementById('insp-title');
    const inspProps = document.getElementById('insp-props');
    const inspRels = document.getElementById('insp-rels');
    const inspRelCount = document.getElementById('insp-rel-count');
    const inspClose = document.getElementById('insp-close');
    const typeFiltersContainer = document.getElementById('type-filters');
    const showChunksCheckbox = document.getElementById('show-chunks-checkbox');
    const chunkCountSpan = document.getElementById('chunk-count');

    // Count chunks
    const chunkNodesCount = RAW_GRAPH.nodes.filter(n => n.group === 'Chunk').length;
    chunkCountSpan.innerText = chunkNodesCount;

    // Build unique groups list
    const allGroups = Array.from(new Set(RAW_GRAPH.nodes.map(n => n.group))).filter(g => g !== 'Chunk');
    allGroups.forEach(g => activeTypeFilters.add(g));

    // Render filter checkboxes
    function renderFilters() {
      typeFiltersContainer.innerHTML = '';
      allGroups.sort().forEach(group => {
        const count = RAW_GRAPH.nodes.filter(n => n.group === group).length;
        const color = RAW_GRAPH.nodes.find(n => n.group === group)?.color?.background || '#64748b';
        
        const row = document.createElement('label');
        row.className = 'flex items-center justify-between cursor-pointer py-1 px-1.5 rounded hover:bg-slate-800/60';
        row.innerHTML = `
          <div class="flex items-center gap-2">
            <input type="checkbox" value="${group}" ${activeTypeFilters.has(group) ? 'checked' : ''} class="type-filter-cb rounded bg-slate-800 border-slate-700 text-cyan-500 focus:ring-0">
            <span class="w-2.5 h-2.5 rounded-full inline-block" style="background-color: ${color}"></span>
            <span class="text-slate-200">${group}</span>
          </div>
          <span class="text-slate-500 text-[11px] font-mono">${count}</span>
        `;
        typeFiltersContainer.appendChild(row);
      });

      document.querySelectorAll('.type-filter-cb').forEach(cb => {
        cb.addEventListener('change', (e) => {
          if (e.target.checked) {
            activeTypeFilters.add(e.target.value);
          } else {
            activeTypeFilters.delete(e.target.value);
          }
          applyFilters();
        });
      });
    }

    document.getElementById('toggle-all-types').addEventListener('click', () => {
      if (activeTypeFilters.size === allGroups.length) {
        activeTypeFilters.clear();
      } else {
        allGroups.forEach(g => activeTypeFilters.add(g));
      }
      renderFilters();
      applyFilters();
    });

    showChunksCheckbox.addEventListener('change', (e) => {
      showChunks = e.target.checked;
      applyFilters();
    });

    function getFilteredData() {
      const filteredNodes = RAW_GRAPH.nodes.filter(node => {
        if (node.group === 'Chunk') return showChunks;
        return activeTypeFilters.has(node.group);
      });

      const nodeIds = new Set(filteredNodes.map(n => n.id));
      const filteredEdges = RAW_GRAPH.edges.filter(e => nodeIds.has(e.from) && nodeIds.has(e.to));

      return { nodes: filteredNodes, edges: filteredEdges };
    }

    function initNetwork() {
      const data = getFilteredData();
      nodesDataSet = new vis.DataSet(data.nodes);
      edgesDataSet = new vis.DataSet(data.edges);

      statsBanner.innerText = `${data.nodes.length} Nodes • ${data.edges.length} Relationships displayed (Total in DB: ${RAW_GRAPH.nodes.length} nodes, ${RAW_GRAPH.edges.length} rels)`;

      const options = {
        nodes: {
          font: {
            color: '#f8fafc',
            size: 13,
            face: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif'
          },
          borderWidth: 2,
          shadow: {
            enabled: true,
            color: 'rgba(0,0,0,0.5)',
            size: 6,
            x: 2,
            y: 2
          }
        },
        edges: {
          font: {
            color: '#94a3b8',
            size: 10,
            align: 'middle',
            background: 'rgba(11, 17, 32, 0.7)'
          },
          color: {
            color: '#334155',
            highlight: '#38bdf8',
            hover: '#64748b'
          },
          arrows: {
            to: { enabled: true, scaleFactor: 0.6 }
          },
          smooth: {
            enabled: true,
            type: 'continuous',
            roundness: 0.2
          }
        },
        physics: {
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -70,
            centralGravity: 0.015,
            springLength: 120,
            springConstant: 0.08,
            damping: 0.8
          },
          stabilization: {
            iterations: 150
          }
        },
        interaction: {
          hover: true,
          tooltipDelay: 150,
          multiselect: true,
          navigationButtons: false
        }
      };

      network = new vis.Network(container, { nodes: nodesDataSet, edges: edgesDataSet }, options);

      network.on('click', onNodeClick);
    }

    function applyFilters() {
      const data = getFilteredData();
      nodesDataSet.clear();
      edgesDataSet.clear();
      nodesDataSet.add(data.nodes);
      edgesDataSet.add(data.edges);
      statsBanner.innerText = `${data.nodes.length} Nodes • ${data.edges.length} Relationships displayed`;
    }

    function onNodeClick(params) {
      if (params.nodes.length === 0) {
        closeInspector();
        resetHighlight();
        return;
      }

      const nodeId = params.nodes[0];
      const node = RAW_GRAPH.nodes.find(n => n.id === nodeId);
      if (!node) return;

      highlightNeighborhood(nodeId);
      openInspector(node);
    }

    function highlightNeighborhood(nodeId) {
      const connectedNodes = new Set(network.getConnectedNodes(nodeId));
      connectedNodes.add(nodeId);

      const allNodes = nodesDataSet.get();
      const updates = allNodes.map(n => {
        if (connectedNodes.has(n.id)) {
          return { id: n.id, opacity: 1.0 };
        } else {
          return { id: n.id, opacity: 0.15 };
        }
      });
      nodesDataSet.update(updates);
    }

    function resetHighlight() {
      const allNodes = nodesDataSet.get();
      const updates = allNodes.map(n => ({ id: n.id, opacity: 1.0 }));
      nodesDataSet.update(updates);
    }

    function openInspector(node) {
      inspBadge.innerText = node.group;
      inspTitle.innerText = node.label;

      // Render properties
      inspProps.innerHTML = '';
      const props = node.properties || {};
      const keys = Object.keys(props).filter(k => k !== 'key' && k !== 'id');
      if (keys.length === 0) {
        inspProps.innerHTML = '<span class="text-slate-500 italic">No additional properties</span>';
      } else {
        keys.sort().forEach(k => {
          let v = props[k];
          if (Array.isArray(v)) v = v.join(', ');
          if (typeof v === 'boolean') v = v ? 'True' : 'False';
          const pRow = document.createElement('div');
          pRow.className = 'flex flex-col pb-1 border-b border-slate-800/60 last:border-0';
          pRow.innerHTML = `
            <span class="text-[10px] text-slate-400 font-mono">${k}</span>
            <span class="text-slate-200 break-words">${v}</span>
          `;
          inspProps.appendChild(pRow);
        });
      }

      // Render connected relationships
      const connectedEdges = RAW_GRAPH.edges.filter(e => e.from === node.id || e.to === node.id);
      inspRelCount.innerText = connectedEdges.length;
      inspRels.innerHTML = '';

      if (connectedEdges.length === 0) {
        inspRels.innerHTML = '<span class="text-slate-500 italic">No connected relationships</span>';
      } else {
        connectedEdges.forEach(e => {
          const isOut = e.from === node.id;
          const otherId = isOut ? e.to : e.from;
          const otherNode = RAW_GRAPH.nodes.find(n => n.id === otherId);
          const otherLabel = otherNode ? otherNode.label : otherId;
          const otherGroup = otherNode ? otherNode.group : '';

          const relCard = document.createElement('div');
          relCard.className = 'bg-slate-900/90 rounded p-2 border border-slate-800 flex flex-col gap-0.5 hover:border-slate-700 cursor-pointer';
          relCard.innerHTML = `
            <div class="flex items-center justify-between text-[10px]">
              <span class="text-cyan-400 font-bold uppercase tracking-wider">${e.label || 'CONNECTED'}</span>
              <span class="text-slate-500 font-mono">${isOut ? '→ OUT' : '← IN'}</span>
            </div>
            <div class="text-slate-200 font-medium truncate">${otherLabel}</div>
            <div class="text-[10px] text-slate-400">${otherGroup}</div>
          `;
          relCard.addEventListener('click', () => {
            network.focus(otherId, { scale: 1.2, animation: true });
            network.selectNodes([otherId]);
            onNodeClick({ nodes: [otherId] });
          });
          inspRels.appendChild(relCard);
        });
      }

      inspectorPanel.classList.remove('translate-x-full');
    }

    function closeInspector() {
      inspectorPanel.classList.add('translate-x-full');
    }

    inspClose.addEventListener('click', () => {
      closeInspector();
      resetHighlight();
    });

    btnFit.addEventListener('click', () => {
      resetHighlight();
      closeInspector();
      network.fit({ animation: true });
    });

    btnPhysics.addEventListener('click', () => {
      physicsEnabled = !physicsEnabled;
      network.setOptions({ physics: { enabled: physicsEnabled } });
      btnPhysics.innerText = physicsEnabled ? 'Freeze Physics' : 'Enable Physics';
      btnPhysics.classList.toggle('bg-amber-600', !physicsEnabled);
    });

    btnToggleFilters.addEventListener('click', () => {
      filterPanel.classList.toggle('hidden');
    });

    // Search logic
    searchInput.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      searchClear.classList.toggle('hidden', !q);
      if (!q) {
        resetHighlight();
        return;
      }

      const match = nodesDataSet.get({
        filter: (n) => n.label.toLowerCase().includes(q) || (n.properties?.title && n.properties.title.toLowerCase().includes(q))
      });

      if (match.length > 0) {
        const targetId = match[0].id;
        network.focus(targetId, { scale: 1.2, animation: true });
        network.selectNodes([targetId]);
        highlightNeighborhood(targetId);
      }
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchClear.classList.add('hidden');
      resetHighlight();
      closeInspector();
    });

    // Run initialization
    renderFilters();
    initNetwork();
  </script>
</body>
</html>
"""


def export_graph_to_html(
    neo4j_uri: str = "bolt://localhost:7688",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "familygraphrag",
    output_html_path: str = "marlow_family_knowledge_graph.html",
    title: str = "Marlow Family Knowledge Graph",
):
    """Query Neo4j and write out the standalone HTML visualization."""
    print(f"Connecting to Neo4j at {neo4j_uri}...")
    driver = GraphDatabase.driver(
        neo4j_uri, auth=(neo4j_user, neo4j_password), notifications_min_severity="OFF"
    )

    nodes = []
    edges = []
    node_id_map = {}

    with driver.session() as session:
        # 1. Fetch Entities
        print("Fetching Entity nodes...")
        ent_res = session.run(
            """
            MATCH (e:Entity)
            RETURN e.name as name, e.type as type, e.family as family,
                   e.relation_to_me as relation, e.email as email, properties(e) as props,
                   elementId(e) as eid
            """
        )
        for r in ent_res:
            name = r["name"] or "Unnamed"
            etype = r["type"] or "Entity"
            is_family = bool(r["family"])

            group = "Person (Family)" if (etype == "Person" and is_family) else etype
            style = TYPE_STYLES.get(group, TYPE_STYLES.get(etype, TYPE_STYLES["Default"]))

            node_obj = {
                "id": r["eid"],
                "label": name,
                "group": group,
                "shape": style["shape"],
                "size": style["size"],
                "color": {
                    "background": style["color"],
                    "border": style["border"],
                    "highlight": {"background": "#ffffff", "border": style["color"]},
                },
                "title": f"<b>{name}</b><br>Type: {etype}" + (f"<br>Relation: {r['relation']}" if r['relation'] else ""),
                "properties": dict(r["props"]),
            }
            nodes.append(node_obj)
            node_id_map[r["eid"]] = r["eid"]

        # 2. Fetch Documents
        print("Fetching Document nodes...")
        doc_res = session.run(
            """
            MATCH (d:Document)
            RETURN d.id as id, d.title as title, d.name as name, d.category as category,
                   d.summary as summary, d.issuer as issuer, d.doc_number as doc_num,
                   properties(d) as props, elementId(d) as eid
            """
        )
        for r in doc_res:
            title_text = r["title"] or r["name"] or r["id"]
            style = TYPE_STYLES["Document"]

            node_obj = {
                "id": r["eid"],
                "label": title_text,
                "group": "Document",
                "shape": style["shape"],
                "size": style["size"],
                "color": {
                    "background": style["color"],
                    "border": style["border"],
                    "highlight": {"background": "#ffffff", "border": style["color"]},
                },
                "title": f"<b>{title_text}</b><br>Category: {r['category']}<br>{(r['summary'] or '')[:120]}...",
                "properties": dict(r["props"]),
            }
            nodes.append(node_obj)
            node_id_map[r["eid"]] = r["eid"]

        # 3. Fetch Categories
        print("Fetching Category nodes...")
        cat_res = session.run(
            """
            MATCH (c:Category)
            RETURN c.name as name, properties(c) as props, elementId(c) as eid
            """
        )
        for r in cat_res:
            cname = r["name"]
            style = TYPE_STYLES["Category"]
            node_obj = {
                "id": r["eid"],
                "label": cname,
                "group": "Category",
                "shape": style["shape"],
                "size": style["size"],
                "color": {
                    "background": style["color"],
                    "border": style["border"],
                    "highlight": {"background": "#ffffff", "border": style["color"]},
                },
                "title": f"Category: {cname}",
                "properties": dict(r["props"]),
            }
            nodes.append(node_obj)
            node_id_map[r["eid"]] = r["eid"]

        # 4. Fetch Chunks (Optional high granularity)
        print("Fetching Chunk nodes...")
        chunk_res = session.run(
            """
            MATCH (c:Chunk)
            RETURN c.id as id, c.locs as locs, properties(c) as props, elementId(c) as eid
            """
        )
        for r in chunk_res:
            cid = r["id"]
            style = TYPE_STYLES["Chunk"]
            node_obj = {
                "id": r["eid"],
                "label": f"Chunk {cid[:8]}",
                "group": "Chunk",
                "shape": style["shape"],
                "size": style["size"],
                "color": {
                    "background": style["color"],
                    "border": style["border"],
                    "highlight": {"background": "#ffffff", "border": style["color"]},
                },
                "title": f"Chunk: {cid}<br>Locations: {r['locs']}",
                "properties": dict(r["props"]),
            }
            nodes.append(node_obj)
            node_id_map[r["eid"]] = r["eid"]

        # 5. Fetch Relationships
        print("Fetching Relationships...")
        rel_res = session.run(
            """
            MATCH (a)-[r]->(b)
            RETURN elementId(a) as from_id, elementId(b) as to_id, type(r) as rtype,
                   r.type as subtype, r.role as role, properties(r) as props, elementId(r) as rid
            """
        )
        for r in rel_res:
            from_id = r["from_id"]
            to_id = r["to_id"]
            rtype = r["rtype"]
            subtype = r["subtype"]
            role = r["role"]

            display_label = subtype or role or rtype
            # Clean up display label
            if rtype == "REL" and subtype:
                display_label = subtype
            elif rtype == "SUBJECT_OF" and role:
                display_label = f"SUBJECT ({role})"
            elif rtype == "IN_CATEGORY":
                display_label = "IN_CATEGORY"
            elif rtype == "HAS_CHUNK":
                display_label = "HAS_CHUNK"
            elif rtype == "ISSUED_BY":
                display_label = "ISSUED_BY"

            edges.append(
                {
                    "id": r["rid"],
                    "from": from_id,
                    "to": to_id,
                    "label": display_label,
                    "title": f"Relationship: {display_label}",
                    "properties": dict(r["props"]),
                }
            )

    driver.close()
    print(f"Exported {len(nodes)} nodes and {len(edges)} relationships.")

    graph_payload = {"nodes": nodes, "edges": edges}
    html_content = HTML_TEMPLATE.replace("__TITLE__", title).replace(
        "__GRAPH_DATA_JSON__", json.dumps(graph_payload)
    )

    out_path = Path(output_html_path)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"Interactive HTML Knowledge Graph saved successfully to: {out_path.resolve()}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Export Neo4j Knowledge Graph to Interactive HTML")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7688", help="Neo4j Bolt URI")
    parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j username")
    parser.add_argument("--neo4j-password", default="familygraphrag", help="Neo4j password")
    parser.add_argument("--output", default="marlow_family_knowledge_graph.html", help="Output HTML file path")
    parser.add_argument("--title", default="Marlow Family Knowledge Graph", help="Title for the visualization")
    args = parser.parse_args()

    export_graph_to_html(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        output_html_path=args.output,
        title=args.title,
    )


if __name__ == "__main__":
    main()
