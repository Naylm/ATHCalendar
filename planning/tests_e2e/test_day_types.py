import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    SCREENSHOT_DIR,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    expect_ok_response,
    seed_period_script,
)


class DayTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    @contextmanager
    def _server(self):
        with LiveServer() as server:
            server.seed(seed_period_script())
            server.seed(
                "from planning.services import periods\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Period\n"
                "p = Period.objects.order_by('start_date').first()\n"
                "periods.set_active(p.pk, actor=Actor.bureau())\n"
            )
            yield server

    def _event_id(self, server, months_offset=0):
        return server.query(
            "from planning.models import Event;"
            "months = sorted({(e.date.year, e.date.month) for e in Event.objects.all()});"
            f"target = months[{months_offset}];"
            "print(Event.objects.filter(date__year=target[0],"
            " date__month=target[1]).order_by('date').first().pk)"
        )

    def _type_id(self, server, name):
        return server.query(
            f"from planning.models import GameType;print(GameType.objects.get(name='{name}').pk)"
        )

    def _page(self, pw, server, width=390):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 844})
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".event-type-select", timeout=8000)
        return browser, tracked

    def test_anonymous_type_select_persists_and_signup_still_works(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            flex = self._type_id(server, "Flex")
            browser, tracked = self._page(pw, server)
            page = tracked.page
            select = page.locator(f"#date-{event_id} .event-type-select")
            with expect_ok_response(page, "PUT", "/type/"):
                select.select_option(flex)
            expect(page.locator(f"#date-{event_id}")).to_contain_text("Flex", timeout=8000)
            assert page.locator(f"#date-{event_id} .event-type-select").input_value() == flex
            form = page.locator(f"#date-{event_id} .name-registration-form")
            form.locator("input[name=name]").fill("Alice")
            with expect_ok_response(page, "PUT", "/registration/"):
                form.locator("button[type=submit]").click()
            expect(page.locator(f"#date-{event_id} .event-names")).to_contain_text(
                "Alice", timeout=8000
            )
            tracked.assert_clean()
            browser.close()

    def test_stale_version_error_resets_choice(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            flex = self._type_id(server, "Flex")
            browser, tracked = self._page(pw, server)
            page = tracked.page
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"events.update_event('{event_id}', actor=Actor.bureau(),"
                " title='Bump')\n"
            )
            select = page.locator(f"#date-{event_id} .event-type-select")
            select.select_option(flex)
            msg = page.locator(f"#date-{event_id} .type-msg")
            msg.wait_for(state="visible", timeout=8000)
            assert msg.get_attribute("role") == "alert"
            assert select.input_value() == ""
            tracked.assert_clean(allow_409=True)
            browser.close()

    def test_network_failure_resets_choice(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            flex = self._type_id(server, "Flex")
            browser, tracked = self._page(pw, server)
            page = tracked.page
            page.route("**/api/events/*/type/", lambda r: r.abort())
            select = page.locator(f"#date-{event_id} .event-type-select")
            select.select_option(flex)
            msg = page.locator(f"#date-{event_id} .type-msg")
            msg.wait_for(state="visible", timeout=8000)
            assert msg.get_attribute("role") == "alert"
            assert select.input_value() == ""
            assert select.is_enabled()
            browser.close()

    def test_collapsed_month_expands_and_signup_reopens(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server, months_offset=1)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            assert not row.is_visible()
            next_btn = page.locator("[data-month-step='1']")
            for _ in range(12):
                if row.is_visible():
                    break
                next_btn.click()
            assert row.is_visible()
            form = row.locator(".name-registration-form")
            form.locator("input[name=name]").fill("Bob")
            with expect_ok_response(page, "PUT", "/registration/"):
                form.locator("button[type=submit]").click()
            expect(page.locator(f"#date-{event_id} .event-names")).to_contain_text(
                "Bob", timeout=8000
            )
            assert page.locator(f"#date-{event_id}").is_visible()
            tracked.assert_clean()
            browser.close()

    def test_viewports_screenshots(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            flex = self._type_id(server, "Flex")
            server.seed(
                "from planning.services import organizers, registrations, events\n"
                "from planning.services.actors import Actor\n"
                "for n in ('Alice', 'Bob'):\n"
                "    o = organizers.get_or_create_organizer(n)\n"
                f"    registrations.set_registration('{event_id}', o.pk,"
                " 'active', actor=Actor.public(o))\n"
                f"events.update_event('{event_id}', actor=Actor.bureau(),"
                f" game_type_id='{flex}')\n"
            )
            for width, name in ((360, "w360"), (390, "w390"), (1440, "w1440")):
                browser, tracked = self._page(pw, server, width=width)
                check_no_horizontal_overflow(tracked.page)
                tracked.page.screenshot(
                    path=str(SCREENSHOT_DIR / f"months-types-{name}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()


if __name__ == "__main__":
    unittest.main()
