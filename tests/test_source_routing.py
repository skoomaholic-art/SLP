import unittest

from parsers.tvplus import ALL_GUIDE_CHANNELS
from services.channel_registry import CHANNELS
from services.source_routing import ROUTES, route_for_channel, runtime_source_names


class SourceRoutingTests(unittest.TestCase):
    def test_exact_14_channels_have_routes_without_duplicates(self):
        names = [channel.name for channel in CHANNELS]
        self.assertEqual(len(names), 14)
        self.assertEqual(len(set(names)), 14)
        self.assertEqual(set(ROUTES), set(names))

    def test_all_14_have_agent_reach_role(self):
        for channel in CHANNELS:
            route = route_for_channel(channel.name)
            self.assertTrue(route.uses_agent_reach, channel.name)
            self.assertTrue(route.agent_reach_role, channel.name)

    def test_provider_backends_are_explicit(self):
        tvplus = [
            channel for channel in CHANNELS
            if channel.guide_backend == "tvplus"
        ]
        mobikino = [
            channel for channel in CHANNELS
            if channel.guide_backend == "mobikino"
        ]
        self.assertEqual(len(tvplus), 12)
        self.assertEqual(
            {channel.name for channel in mobikino},
            {"EUROSPORT 1", "EUROSPORT 2"},
        )

    def test_tvguide_parser_is_generated_from_registry(self):
        expected = {
            channel.tvplus_name
            for channel in CHANNELS
            if channel.tvplus_id
        }
        actual = {channel.name for channel in ALL_GUIDE_CHANNELS}
        self.assertEqual(len(ALL_GUIDE_CHANNELS), 14)
        self.assertEqual(actual, expected)

    def test_vsetv_is_only_used_where_verified_id_exists(self):
        with_vsetv = [channel for channel in CHANNELS if channel.vsetv_id is not None]
        self.assertEqual(len(with_vsetv), 9)
        for channel in CHANNELS:
            route = route_for_channel(channel.name)
            has_fallback = "vsetv_live_badge" in route.fallback_transports
            self.assertEqual(has_fallback, channel.vsetv_id is not None, channel.name)

    def test_supplier_xlsx_is_primary_only_for_supplier_channels(self):
        supplier_channels = {
            channel.name for channel in CHANNELS if channel.supplier_source
        }
        self.assertEqual(len(supplier_channels), 8)
        for channel in CHANNELS:
            route = route_for_channel(channel.name)
            if route.primary_transport == "supplier_xlsx":
                self.assertIn(channel.name, supplier_channels)

    def test_primary_transport_is_not_repeated_as_fallback(self):
        for channel in CHANNELS:
            route = route_for_channel(channel.name)
            self.assertNotIn(
                route.primary_transport,
                route.fallback_transports,
                channel.name,
            )

    def test_qazsport_has_direct_agent_reach_page_fallback(self):
        route = route_for_channel("QAZSPORT HD")
        self.assertEqual(route.primary_transport, "official_qazsport_html")
        self.assertIn("agent_reach_official_page", route.fallback_transports)

    def test_q_channels_fail_closed_through_exact_championat_match(self):
        for name in ("Q LEAGUE", "Q FOOTBALL", "Q ARENA"):
            route = route_for_channel(name)
            self.assertEqual(route.confirmation_transports, ("championat_exact_match",))
            self.assertEqual(
                route.agent_reach_role,
                "championat_reader_for_exact_match",
            )

    def test_runtime_source_names_follow_available_transports(self):
        by_name = {channel.name: runtime_source_names(channel) for channel in CHANNELS}
        self.assertIn("qazsport", by_name["QAZSPORT HD"])
        self.assertIn("sportplus", by_name["SPORT+ Qazaqstan"])
        self.assertIn("tvguide", by_name["EUROSPORT 1"])
        self.assertTrue(
            any(name.startswith("web_vsetv_") for name in by_name["KHL HD"])
        )
        self.assertTrue(
            any(name.startswith("web_vsetv_") for name in by_name["SETANTA SPORTS KZ"])
        )
        self.assertTrue(
            any(name.startswith("web_iptvx_") for name in by_name["SETANTA SPORTS KZ"])
        )
        self.assertTrue(
            any(name.startswith("web_iptvx_") for name in by_name["QAZSPORT HD"])
        )
        self.assertFalse(
            any(name.startswith("web_iptvx_") for name in by_name["KHL PRIME"])
        )


if __name__ == "__main__":
    unittest.main()
