import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    BUREAU_PASSWORD,
    SCREENSHOT_DIR,
    SEED_BUREAU,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    expect_ok_response,
    seed_period_script,
)


class Release020Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    @contextmanager
    def _server(self):
        with LiveServer() as server:
            server.seed(seed_period_script())
            server.seed(SEED_BUREAU)
            server.seed(
                "from planning.models import Event, GameType\n"
                "from planning.services import events, organizers, registrations, types\n"
                "from planning.services.actors import Actor\n"
                "bureau = Actor.bureau()\n"
                "dates = list(Event.objects.order_by('date')[:3])\n"
                "flex = GameType.objects.get(code='flex')\n"
                "types.update_type(flex.pk, actor=bureau, color='#60766b')\n"
                "for event in dates:\n"
                "    events.update_event(event.pk, actor=bureau, game_type_id=flex.pk)\n"
                "alice = organizers.get_or_create_organizer('Alice')\n"
                "registrations.set_registration(dates[0].pk, alice.pk, 'active', actor=bureau)\n"
                "for event, state in zip(dates, ('full', 'unavailable', 'available')):\n"
                "    event.refresh_from_db()\n"
                "    events.set_event_availability(event.pk, actor=bureau,"
                " availability=state, expected_version=event.version)\n"
            )
            yield server

    def test_mobile_states_and_color_visibility(self):
        with self._server() as server, browser_tests()() as pw:
            browser = pw.chromium.launch()
            try:
                for width in (320, 390, 1440):
                    context = browser.new_context(
                        viewport={"width": width, "height": 900},
                        is_mobile=width < 768,
                        has_touch=width < 768,
                    )
                    tracked = TrackedPage(context.new_page())
                    page = tracked.page
                    page.goto(server.url)
                    rows = page.locator("#event-list .event-card")
                    expect(rows.first).to_contain_text("Complète")
                    expect(rows.nth(1)).to_contain_text("Indisponible")
                    expect(rows.first.locator(".name-registration-form")).to_have_count(0)
                    expect(rows.nth(1).locator(".event-type-select")).to_have_count(0)
                    select = rows.nth(2).locator(".event-type-select")
                    expect(select).to_have_css("background-color", "rgb(96, 118, 107)")
                    check_no_horizontal_overflow(page)
                    page.screenshot(path=str(SCREENSHOT_DIR / f"release020-planning-{width}.png"))
                    expect(page.get_by_role("link", name="Par personne", exact=True)).to_have_count(
                        0
                    )
                    check_no_horizontal_overflow(page)
                    tracked.assert_clean()
                    context.close()
            finally:
                browser.close()

    def test_bureau_color_change_reopening_and_table_exports(self):
        with self._server() as server, browser_tests()() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
                )
                tracked = TrackedPage(context.new_page())
                page = tracked.page
                page.goto(server.url + "/bureau/connexion/")
                page.locator('input[name="password"]').fill(BUREAU_PASSWORD)
                page.get_by_role("button", name="Connexion", exact=True).click()
                expect(page).to_have_url(server.url + "/bureau/")
                page.get_by_role("link", name="Gérer", exact=True).first.click()
                first_form = page.locator(".availability-form").first
                first_form.locator("select").select_option("available")
                first_form.get_by_role("button", name="Enregistrer", exact=True).click()
                expect(page.locator(".availability-form").first.locator("select")).to_have_value(
                    "available"
                )
                check_no_horizontal_overflow(page)

                page.get_by_role("link", name="Types", exact=True).click()
                form = page.locator("form.inline-form").filter(
                    has=page.locator('input[name="name"][value="Flex"]')
                )
                form.get_by_label("Ardoise", exact=True).check()
                form.get_by_role("button", name="Modifier", exact=True).click()
                check_no_horizontal_overflow(page)
                page.screenshot(path=str(SCREENSHOT_DIR / "release020-color-picker.png"))
                page.goto(server.url)
                first = page.locator(".event-type-select").first
                expect(first).to_have_css("background-color", "rgb(82, 103, 119)")
                roltac = server.query(
                    "from planning.models import GameType; "
                    "print(GameType.objects.get(code='roltac').pk)"
                )
                with expect_ok_response(page, "PUT", "/type/"):
                    first.select_option(roltac)
                expect(page.locator(".event-type-select").first).to_have_value(roltac)
                expect(page.locator(".event-type-select").first).to_have_css(
                    "background-color", "rgb(176, 138, 62)"
                )
                page.reload()
                expect(page.locator(".event-type-select").first).to_have_css(
                    "background-color", "rgb(176, 138, 62)"
                )
                page.goto(server.url + "/bureau/")
                page.get_by_role("link", name="Gérer", exact=True).first.click()
                options_summary = page.get_by_text("Options", exact=True)
                list_pos = page.evaluate(
                    "document.querySelector('details.card')"
                    ".compareDocumentPosition(document.querySelector('.event-list'))"
                )
                assert list_pos & 4, "Options doit précéder la liste des dates"
                expect(options_summary).to_be_visible()
                box = options_summary.bounding_box()
                assert box["y"] < 844, box
                options_summary.click()
                with page.expect_download() as download:
                    page.get_by_role("link", name="Tableau Excel", exact=True).click()
                assert download.value.suggested_filename.endswith(".xlsx")
                assert download.value.failure() is None
                # Chromium hands downloads off as aborted page requests.
                download_url = download.value.url
                tracked.js_errors = [
                    error
                    for error in tracked.js_errors
                    if error != f"requestfailed: {download_url}"
                ]
                href = page.get_by_role("link", name="Tableau imprimable").get_attribute("href")
                page.goto(server.url + href)
                expect(page.locator(".report-table tbody tr")).not_to_have_count(0)
                page.set_viewport_size({"width": 1440, "height": 900})
                page.emulate_media(media="print")
                expect(page.locator(".site-header")).not_to_be_visible()
                page.screenshot(path=str(SCREENSHOT_DIR / "release020-print.png"))
                tracked.assert_clean()
                context.close()
            finally:
                browser.close()
