import unittest

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    SCREENSHOT_DIR,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    seed_period_script,
)


class TypeGuideUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def test_compact_guide_mouse_keyboard_and_viewports(self):
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(seed_period_script())
            server.seed(
                "from planning.models import Period\n"
                "from planning.services import periods\n"
                "from planning.services.actors import Actor\n"
                "periods.set_active(Period.objects.first().pk, actor=Actor.bureau())\n"
            )
            browser = pw.chromium.launch()
            try:
                for width in (320, 390, 1440):
                    with self.subTest(width=width):
                        page = browser.new_page(viewport={"width": width, "height": 900})
                        page.set_default_timeout(5000)
                        tracked = TrackedPage(page)
                        mutations = []
                        page.on(
                            "request",
                            lambda request, recorded=mutations: (
                                recorded.append(request.method)
                                if request.method in {"POST", "PUT", "PATCH", "DELETE"}
                                else None
                            ),
                        )
                        page.goto(server.url + "/")
                        guide = page.locator("#type-guide")
                        summary = guide.locator("summary")
                        panel = guide.locator(".type-guide-panel")
                        expect(summary).to_be_visible()
                        self.assertIsNone(guide.get_attribute("open"))
                        expect(panel).not_to_be_visible()
                        check_no_horizontal_overflow(page)
                        page.screenshot(path=str(SCREENSHOT_DIR / f"type-guide-closed-{width}.png"))
                        row = page.locator(".event-card").first
                        before = row.bounding_box()["y"]
                        summary.click()
                        expect(panel).to_be_visible()
                        expect(panel).to_contain_text("Parties ouvertes à tous")
                        expect(panel).to_contain_text("Parties plus scénarisées")
                        self.assertAlmostEqual(row.bounding_box()["y"], before, delta=1)
                        check_no_horizontal_overflow(page)
                        page.screenshot(path=str(SCREENSHOT_DIR / f"type-guide-open-{width}.png"))
                        page.locator("#planning-title").click()
                        expect(panel).not_to_be_visible()
                        summary.focus()
                        summary.press("Enter")
                        expect(panel).to_be_visible()
                        page.keyboard.press("Escape")
                        expect(panel).not_to_be_visible()
                        expect(summary).to_be_focused()
                        self.assertEqual(mutations, [])
                        tracked.assert_clean()
                        page.close()
            finally:
                browser.close()
