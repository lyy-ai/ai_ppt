import json
import unittest

from cloud_generator.brief_schema import Brief
from cloud_generator.core_adapter import CoreRouteUnavailable, resolve_route, route_capabilities


class CoreAdapterTests(unittest.TestCase):
    def test_default_brief_uses_generate_route(self):
        brief = Brief(source_text="A short source")
        self.assertEqual(brief.route, "generate_pptx")
        self.assertEqual(brief.validate(), [])

    def test_route_capabilities_have_all_upstream_routes(self):
        self.assertEqual(
            set(route_capabilities()),
            {"generate_pptx", "create_template", "fill_native_pptx", "enhance_native_pptx"},
        )

    def test_core_routes_are_present_but_cloud_adapters_are_explicitly_gated(self):
        capabilities = route_capabilities()
        self.assertTrue(capabilities["fill_native_pptx"].core_available)
        self.assertFalse(capabilities["fill_native_pptx"].cloud_available)
        self.assertTrue(capabilities["fill_native_pptx"].session_available)
        self.assertTrue(capabilities["enhance_native_pptx"].core_available)
        self.assertFalse(capabilities["enhance_native_pptx"].cloud_available)
        self.assertTrue(capabilities["enhance_native_pptx"].session_available)
        with self.assertRaises(CoreRouteUnavailable):
            resolve_route("enhance_native_pptx")

    def test_brief_schema_round_trips_route(self):
        brief = Brief.from_dict({"source_text": "source", "route": "generate_pptx"})
        self.assertEqual(json.loads(brief.to_json())["route"], "generate_pptx")


if __name__ == "__main__":
    unittest.main()
