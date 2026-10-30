"""
Navigable maritime graph network for the Arabian Sea, Indian Ocean, and coastal corridors.
Implemented in pure Python using Dijkstra / A* priority queue algorithm with zero external C-dependencies.
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, List, Tuple, Sequence, Set, Any, Iterator

from app.routing.geometry import (
    haversine_distance_nm,
    segment_intersects_polygon,
)

# Standard Navigable Corridors and Deep-Water Waypoints
# Formulated to preserve international maritime navigation separation and avoid shoals/land.
MARITIME_WAYPOINTS: Dict[str, Tuple[float, float, str]] = {
    # Mumbai & Central Arabian Sea Hub
    "mumbai_port": (18.940, 72.840, "Mumbai Port Entrance"),
    "jnpt_port": (18.950, 72.950, "JNPT Terminal"),
    "mumbai_fairway": (18.880, 72.650, "Mumbai Approach Fairway"),
    "mumbai_tss_outer": (18.900, 72.100, "Mumbai Outer TSS"),
    "mumbai_deepwater": (19.000, 71.500, "Mumbai Deepwater Channel"),
    "arabian_sea_north": (19.500, 71.200, "Arabian Sea North Sector"),
    "arabian_sea_mid": (19.050, 71.200, "Arabian Sea Central Sector (Slick Area)"),
    "arabian_sea_south": (18.500, 71.200, "Arabian Sea South Sector"),
    "arabian_sea_west": (19.050, 70.400, "Arabian Sea West Outer Passage"),
    "arabian_sea_east": (19.050, 71.900, "Arabian Sea Inshore Passage"),
    "arabian_sea_nw": (19.600, 70.500, "Arabian Sea NW Passage"),
    "arabian_sea_sw": (18.400, 70.500, "Arabian Sea SW Passage"),
    "arabian_sea_ne": (19.600, 71.900, "Arabian Sea NE Passage"),
    "arabian_sea_se": (18.400, 71.900, "Arabian Sea SE Passage"),

    # Gujarat & Gulf of Kutch Corridor
    "kandla_port": (23.010, 70.220, "Kandla Port"),
    "kutch_inner": (22.800, 69.800, "Gulf of Kutch Inner"),
    "kutch_outer": (22.450, 68.800, "Gulf of Kutch Outer Fairway"),
    "kathiawar_offshore_n": (21.500, 68.800, "Kathiawar Offshore North"),
    "kathiawar_offshore_s": (20.300, 69.800, "Kathiawar Offshore South"),
    "gujarat_deepwater": (20.000, 70.800, "Gujarat Approach Deepwater"),

    # Southern Coastal Corridor (Goa, Mangalore, Kochi, Colombo)
    "konkan_north": (17.500, 72.400, "Konkan North Deepwater"),
    "konkan_mid": (16.500, 72.600, "Konkan Central Deepwater"),
    "mormugao_port": (15.415, 73.790, "Mormugao Port"),
    "goa_fairway": (15.400, 73.200, "Goa Approach Fairway"),
    "goa_deepwater": (15.000, 73.000, "Goa Deepwater Corridor"),
    "mangalore_port": (12.925, 74.810, "New Mangalore Port"),
    "mangalore_fairway": (12.900, 74.300, "Mangalore Fairway"),
    "malabar_deepwater": (11.500, 74.500, "Malabar Deepwater Corridor"),
    "kochi_port": (9.965, 76.260, "Cochin Port"),
    "kochi_fairway": (9.950, 75.800, "Kochi Approach Fairway"),
    "lakshadweep_sea": (8.500, 76.200, "Lakshadweep Sea Passage"),
    "cape_comorin_offshore": (7.400, 77.200, "Cape Comorin Offshore TSS"),
    "colombo_port": (6.950, 79.840, "Colombo Port"),
    "colombo_fairway": (6.900, 79.400, "Colombo Approach Fairway"),
    "sri_lanka_south": (5.800, 80.500, "Dondra Head TSS / Sri Lanka South"),

    # Middle East / Persian Gulf / Red Sea Corridors
    "oman_deepwater_e": (21.000, 63.500, "Central Arabian Sea Ocean Route"),
    "gulf_oman_entry": (24.000, 59.000, "Gulf of Oman Outer Approach"),
    "hormuz_approach": (25.500, 57.000, "Strait of Hormuz Approach"),
    "dubai_port": (25.010, 55.060, "Jebel Ali / Dubai"),
    "salalah_port": (16.940, 54.000, "Salalah Port"),
    "salalah_fairway": (16.800, 54.200, "Salalah Fairway"),
    "gulf_aden_corridor": (14.000, 51.000, "Gulf of Aden IRTC Corridor"),

    # Eastbound Malacca Corridor
    "bay_of_bengal_south": (5.800, 85.000, "Bay of Bengal South TSS"),
    "malacca_nw_entry": (5.500, 95.000, "Malacca Strait NW Entry"),
    "malacca_mid": (2.800, 101.200, "Malacca Strait Central"),
    "singapore_fairway": (1.200, 103.600, "Singapore Western Approaches"),
    "singapore_port": (1.260, 103.820, "Port of Singapore"),
}

# Established Navigable Edges (bidirectional)
MARITIME_EDGES: List[Tuple[str, str]] = [
    # Mumbai local access
    ("mumbai_port", "mumbai_fairway"),
    ("jnpt_port", "mumbai_fairway"),
    ("mumbai_fairway", "mumbai_tss_outer"),
    ("mumbai_tss_outer", "mumbai_deepwater"),

    # Arabian Sea Central Grid (around demo spill area 19.05, 71.20)
    ("mumbai_deepwater", "arabian_sea_east"),
    ("mumbai_deepwater", "arabian_sea_north"),
    ("mumbai_deepwater", "arabian_sea_south"),
    ("mumbai_deepwater", "arabian_sea_mid"),

    ("arabian_sea_north", "arabian_sea_mid"),
    ("arabian_sea_mid", "arabian_sea_south"),
    ("arabian_sea_west", "arabian_sea_mid"),
    ("arabian_sea_mid", "arabian_sea_east"),

    # Ring around central spill area for smooth detour
    ("arabian_sea_north", "arabian_sea_nw"),
    ("arabian_sea_nw", "arabian_sea_west"),
    ("arabian_sea_west", "arabian_sea_sw"),
    ("arabian_sea_sw", "arabian_sea_south"),
    ("arabian_sea_south", "arabian_sea_se"),
    ("arabian_sea_se", "arabian_sea_east"),
    ("arabian_sea_east", "arabian_sea_ne"),
    ("arabian_sea_ne", "arabian_sea_north"),

    # North to Gujarat
    ("arabian_sea_nw", "gujarat_deepwater"),
    ("arabian_sea_north", "gujarat_deepwater"),
    ("gujarat_deepwater", "kathiawar_offshore_s"),
    ("kathiawar_offshore_s", "kathiawar_offshore_n"),
    ("kathiawar_offshore_n", "kutch_outer"),
    ("kutch_outer", "kutch_inner"),
    ("kutch_inner", "kandla_port"),

    # South to Konkan & Goa
    ("arabian_sea_south", "konkan_north"),
    ("arabian_sea_se", "konkan_north"),
    ("konkan_north", "konkan_mid"),
    ("konkan_mid", "goa_deepwater"),
    ("goa_deepwater", "goa_fairway"),
    ("goa_fairway", "mormugao_port"),

    # South to Mangalore & Kerala
    ("goa_deepwater", "mangalore_fairway"),
    ("mangalore_fairway", "mangalore_port"),
    ("mangalore_fairway", "malabar_deepwater"),
    ("malabar_deepwater", "kochi_fairway"),
    ("kochi_fairway", "kochi_port"),
    ("kochi_fairway", "lakshadweep_sea"),
    ("lakshadweep_sea", "cape_comorin_offshore"),

    # Sri Lanka & South East
    ("cape_comorin_offshore", "colombo_fairway"),
    ("colombo_fairway", "colombo_port"),
    ("colombo_fairway", "sri_lanka_south"),
    ("sri_lanka_south", "bay_of_bengal_south"),
    ("bay_of_bengal_south", "malacca_nw_entry"),
    ("malacca_nw_entry", "malacca_mid"),
    ("malacca_mid", "singapore_fairway"),
    ("singapore_fairway", "singapore_port"),

    # West to Middle East & Red Sea
    ("arabian_sea_west", "oman_deepwater_e"),
    ("arabian_sea_nw", "oman_deepwater_e"),
    ("gujarat_deepwater", "oman_deepwater_e"),
    ("oman_deepwater_e", "gulf_oman_entry"),
    ("gulf_oman_entry", "hormuz_approach"),
    ("hormuz_approach", "dubai_port"),
    ("oman_deepwater_e", "salalah_fairway"),
    ("salalah_fairway", "salalah_port"),
    ("salalah_fairway", "gulf_aden_corridor"),
    ("goa_deepwater", "oman_deepwater_e"),
]


class NodeView(dict):
    """Provides networkx-compatible node dict access and g.nodes(data=True) calls."""

    def __call__(self, data: bool = False) -> List[Any]:
        if data:
            return list(self.items())
        return list(self.keys())


class EdgeView(list):
    """Provides networkx-compatible edge list access and g.edges() calls."""

    def __call__(self) -> EdgeView:
        return self


class MaritimeGraph:
    """
    Pure-Python graph data structure with Dijkstra shortest path.
    Requires no external C-extensions or networkx installation.
    """

    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self.adj: Dict[str, Dict[str, float]] = {}

    @property
    def nodes(self) -> NodeView:
        return NodeView(self._nodes)

    def add_node(self, node_id: str, lat: float, lon: float, label: str = "") -> None:
        self._nodes[node_id] = {"lat": lat, "lon": lon, "label": label}
        if node_id not in self.adj:
            self.adj[node_id] = {}

    def add_edge(self, u: str, v: str, weight: float, distance_nm: float | None = None) -> None:
        dist = weight if distance_nm is None else distance_nm
        if u not in self.adj:
            self.adj[u] = {}
        if v not in self.adj:
            self.adj[v] = {}
        self.adj[u][v] = dist
        self.adj[v][u] = dist

    def remove_edge(self, u: str, v: str) -> None:
        if u in self.adj:
            self.adj[u].pop(v, None)
        if v in self.adj:
            self.adj[v].pop(u, None)

    @property
    def edges(self) -> EdgeView:
        seen: Set[Tuple[str, str]] = set()
        edge_list: List[Tuple[str, str]] = []
        for u, neighbors in self.adj.items():
            for v in neighbors:
                if (v, u) not in seen:
                    seen.add((u, v))
                    edge_list.append((u, v))
        return EdgeView(edge_list)

    def copy(self) -> MaritimeGraph:
        new_g = MaritimeGraph()
        new_g._nodes = {k: dict(v) for k, v in self._nodes.items()}
        new_g.adj = {u: dict(neighbors) for u, neighbors in self.adj.items()}
        return new_g

    def shortest_path(self, source: str, target: str) -> List[str]:
        if source not in self._nodes or target not in self._nodes:
            raise KeyError(f"Source ({source}) or target ({target}) not in graph")

        dist: Dict[str, float] = {source: 0.0}
        prev: Dict[str, str | None] = {}
        visited: Set[str] = set()
        pq: List[Tuple[float, str]] = [(0.0, source)]

        while pq:
            d, u = heapq.heappop(pq)
            if u == target:
                break
            if u in visited:
                continue
            visited.add(u)

            for v, weight in self.adj.get(u, {}).items():
                if v in visited:
                    continue
                new_d = d + weight
                if new_d < dist.get(v, float("inf")):
                    dist[v] = new_d
                    prev[v] = u
                    heapq.heappush(pq, (new_d, v))

        if target not in prev and source != target:
            raise ValueError(f"No path between {source} and {target}")

        path: List[str] = []
        curr: str | None = target
        while curr:
            path.append(curr)
            curr = prev.get(curr)
        path.reverse()
        return path


def build_base_maritime_graph() -> MaritimeGraph:
    """Builds the baseline navigable maritime graph with edge weights in Nautical Miles."""
    g = MaritimeGraph()
    for node_id, (lat, lon, label) in MARITIME_WAYPOINTS.items():
        g.add_node(node_id, lat=lat, lon=lon, label=label)

    for u, v in MARITIME_EDGES:
        if u in g.nodes and v in g.nodes:
            lat1, lon1 = g.nodes[u]["lat"], g.nodes[u]["lon"]
            lat2, lon2 = g.nodes[v]["lat"], g.nodes[v]["lon"]
            dist_nm = haversine_distance_nm(lat1, lon1, lat2, lon2)
            g.add_edge(u, v, weight=dist_nm, distance_nm=dist_nm)

    return g


def find_nearest_navigable_node(
    lat: float, lon: float, graph: MaritimeGraph | None = None
) -> Tuple[str, float]:
    """Finds closest node in the maritime graph to coordinates (lat, lon)."""
    g = graph or build_base_maritime_graph()
    best_node = None
    min_dist = float("inf")

    for node_id, data in g.nodes.items():
        dist = haversine_distance_nm(lat, lon, data["lat"], data["lon"])
        if dist < min_dist:
            min_dist = dist
            best_node = node_id

    return best_node, min_dist


def connect_custom_point(
    graph: MaritimeGraph,
    point_id: str,
    lat: float,
    lon: float,
    label: str = "Custom Waypoint",
    k_nearest: int = 4,
    spill_polygons: Sequence[Sequence[Sequence[float]]] | None = None,
) -> MaritimeGraph:
    """
    Connects a custom point (e.g. user-selected port or coordinates) to the k nearest
    navigable nodes, ensuring connecting segments do not penetrate spill zones.
    """
    g = graph.copy()
    g.add_node(point_id, lat=lat, lon=lon, label=label)

    # Sort nodes by proximity
    nodes_by_dist = []
    for node_id, data in graph.nodes.items():
        if node_id == point_id:
            continue
        dist = haversine_distance_nm(lat, lon, data["lat"], data["lon"])
        nodes_by_dist.append((dist, node_id, data["lat"], data["lon"]))

    nodes_by_dist.sort(key=lambda x: x[0])

    connected = 0
    for dist, node_id, n_lat, n_lon in nodes_by_dist:
        # Check if edge intersects any spill polygon
        intersects = False
        if spill_polygons:
            for poly in spill_polygons:
                if segment_intersects_polygon([lat, lon], [n_lat, n_lon], poly):
                    intersects = True
                    break

        if not intersects:
            g.add_edge(point_id, node_id, weight=dist, distance_nm=dist)
            connected += 1
            if connected >= k_nearest:
                break

    # Fallback if all nearest were blocked by spill
    if connected == 0 and nodes_by_dist:
        closest = nodes_by_dist[0]
        g.add_edge(point_id, closest[1], weight=closest[0] * 5.0, distance_nm=closest[0])

    return g
