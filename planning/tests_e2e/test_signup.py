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


class NameSignupTests(unittest.TestCase):
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

    def _first_event_id(self, server):
        return server.query(
            "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
        )

    def _page(self, pw, server, width=390):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 844})
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".name-registration-form", timeout=8000)
        return browser, tracked

    def _submit(self, page, event_id, name):
        row = page.locator(f"#date-{event_id}")
        form = row.locator(".name-registration-form")
        form.locator("input[name=name]").fill(name)
        form.locator("button[type=submit]").click()

    def _names(self, page, event_id):
        return page.locator(f"#date-{event_id} .event-names")

    def test_name_signup_same_date_no_picker_no_storage(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Alice")
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            assert page.evaluate("document.getElementById('pseudo-modal')") is None
            assert page.evaluate("localStorage.length") == 0
            page.reload()
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            tracked.assert_clean()
            browser.close()

    def test_second_name_same_context_and_duplicate_idempotent(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Alice")
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Bob")
            expect(self._names(page, event_id)).to_contain_text("Bob", timeout=8000)
            names = self._names(page, event_id).text_content()
            assert "Alice" in names and "Bob" in names, names
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Alice")
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            count = server.query(
                "from planning.models import Registration;print(Registration.objects.filter("
                f"event_id='{event_id}', state='active').count())"
            )
            assert count == "2", count
            tracked.assert_clean()
            browser.close()

    def test_special_chars_name_rendered_as_text(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "L'O<u>")
            expect(self._names(page, event_id)).to_contain_text("L'O<u>", timeout=8000)
            assert (
                page.evaluate(f"document.querySelector('#date-{event_id} .event-names u')") is None
            )
            tracked.assert_clean()
            browser.close()

    def test_network_put_abort_keeps_input_and_shows_error(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            page.route("**/api/events/*/registration/", lambda r: r.abort())
            self._submit(page, event_id, "Alice")
            msg = page.locator(f"#date-{event_id} .form-msg")
            msg.wait_for(state="visible", timeout=8000)
            assert msg.get_attribute("role") == "alert"
            assert page.locator(f"#date-{event_id} input[name=name]").input_value() == "Alice"
            assert page.locator(f"#date-{event_id} button[type=submit]").is_enabled()
            count = server.query(
                "from planning.models import Registration;print(Registration.objects.count())"
            )
            assert count == "0", count
            browser.close()

    def test_stale_event_version_conflict_no_registration(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, tracked = self._page(pw, server)
            page = tracked.page
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"events.set_registrations_open('{event_id}',"
                " actor=Actor.bureau(), is_open=False)\n"
                f"events.set_registrations_open('{event_id}',"
                " actor=Actor.bureau(), is_open=True)\n"
            )
            self._submit(page, event_id, "Alice")
            msg = page.locator(f"#date-{event_id} .form-msg")
            msg.wait_for(state="visible", timeout=8000)
            assert msg.get_attribute("role") == "alert"
            count = server.query(
                "from planning.models import Registration;print(Registration.objects.count())"
            )
            assert count == "0", count
            tracked.assert_clean(allow_409=True)
            browser.close()

    def test_stored_ath_pseudo_ignored(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script(
                "localStorage.setItem('ath_pseudo', JSON.stringify({id: 'old-id', name: 'Zed'}))"
            )
            tracked = TrackedPage(context.new_page())
            page = tracked.page
            page.goto(server.url + "/")
            page.wait_for_selector(".name-registration-form", timeout=8000)
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Alice")
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            assert page.evaluate("document.getElementById('pseudo-modal')") is None
            assert page.evaluate("document.getElementById('acting-bar')") is None
            stored = page.evaluate("localStorage.getItem('ath_pseudo')")
            assert '"Zed"' in stored, stored
            tracked.assert_clean()
            browser.close()

    def test_viewports_no_overflow_screenshots(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            server.seed(
                "from planning.services import organizers, registrations\n"
                "from planning.services.actors import Actor\n"
                "for n in ('Alice', 'Bob'):\n"
                "    o = organizers.get_or_create_organizer(n)\n"
                f"    registrations.set_registration('{event_id}', o.pk,"
                " 'active', actor=Actor.public(o))\n"
            )
            for width, name in ((360, "w360"), (390, "w390"), (1440, "w1440")):
                browser, tracked = self._page(pw, server, width=width)
                check_no_horizontal_overflow(tracked.page)
                tracked.page.screenshot(
                    path=str(SCREENSHOT_DIR / f"signup-compact-{name}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()


if __name__ == "__main__":
    unittest.main()
