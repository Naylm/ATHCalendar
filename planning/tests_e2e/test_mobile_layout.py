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

VIEWPORTS = ((320, 740), (360, 800), (390, 844), (430, 932))


class MobileLayoutTests(unittest.TestCase):
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

    def _event_id(self, server, index=0):
        return server.query(
            f"from planning.models import Event;print(Event.objects.order_by('date')[{index}].pk)"
        )

    def _seed_names_and_type(self, server, event_id, names=("Alice", "Bob")):
        names_src = repr(tuple(names))
        server.seed(
            "from planning.services import organizers, registrations, events\n"
            "from planning.services.actors import Actor\n"
            "from planning.models import GameType\n"
            f"for n in {names_src}:\n"
            "    o = organizers.get_or_create_organizer(n)\n"
            f"    registrations.set_registration('{event_id}', o.pk,"
            " 'active', actor=Actor.public(o))\n"
            f"events.update_event('{event_id}', actor=Actor.bureau(),"
            " game_type_id=str(GameType.objects.get(name='Flex').pk))\n"
        )

    def _page(self, pw, server, width, height):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": height})
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".name-registration-form", timeout=8000)
        return browser, tracked

    def test_mobile_viewports_layout(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            self._seed_names_and_type(server, event_id)
            for width, height in VIEWPORTS:
                browser, tracked = self._page(pw, server, width, height)
                page = tracked.page
                check_no_horizontal_overflow(page)
                header = page.locator(".header-inner").bounding_box()
                assert header["height"] <= 84, (width, header)
                assert page.locator(".brand").is_visible()
                assert page.locator(".top-nav a[href='/bureau/']").is_visible()
                row = page.locator(f"#date-{event_id}")
                dateline = row.locator(".event-dateline").bounding_box()
                select = row.locator(".event-type-select").bounding_box()
                assert dateline["y"] <= select["y"] + select["height"]
                assert select["y"] <= dateline["y"] + dateline["height"], (
                    width,
                    dateline,
                    select,
                )
                assert row.locator(".date-number").is_visible()
                assert row.locator(".date-weekday").is_visible()
                date_box = row.locator(".event-date").bounding_box()
                assert date_box is None or date_box["width"] <= 1, date_box
                input_el = row.locator("input[name=name]")
                button = row.locator("button[type=submit]")
                assert input_el.is_visible() and button.is_visible()
                assert input_el.bounding_box()["height"] >= 44
                assert button.bounding_box()["height"] >= 44
                font = input_el.evaluate("el => getComputedStyle(el).fontSize")
                assert float(font.replace("px", "")) >= 16
                names = row.locator(".event-names")
                expect(names).to_contain_text("Alice")
                expect(names).to_contain_text("Bob")
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"mobile-friendly-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()
            browser, tracked = self._page(pw, server, 1440, 900)
            check_no_horizontal_overflow(tracked.page)
            tracked.page.screenshot(
                path=str(SCREENSHOT_DIR / "mobile-friendly-1440.png"),
                full_page=False,
            )
            tracked.assert_clean()
            browser.close()

    def test_long_names_and_type_no_overflow(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            long_name = "Prénom-Très-Long-Nom-De-Famille-Quarante"
            self._seed_names_and_type(server, event_id, names=(long_name, "Bob"))
            type_id = server.query(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import GameType\n"
                "t = GameType.objects.create(name='TypeDePartieAuNomTresLongPourTester',"
                " code='long-type-test', color='#6b7c4a', is_active=True)\n"
                "print(t.pk)\n"
            )
            for width, height in ((320, 740), (390, 844)):
                browser, tracked = self._page(pw, server, width, height)
                page = tracked.page
                page.locator(f"#date-{event_id} .event-type-select").select_option(type_id)
                page.wait_for_timeout(2500)
                check_no_horizontal_overflow(page)
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"mobile-long-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def test_mobile_signup_month_expand_and_type(self):
        with self._server() as server, browser_tests()() as pw:
            first_id = self._event_id(server)
            second_month_id = server.query(
                "from planning.models import Event\n"
                "from itertools import groupby\n"
                "events = list(Event.objects.order_by('date'))\n"
                "months = [list(g) for _, g in groupby(events, "
                "key=lambda e: (e.date.year, e.date.month))]\n"
                "print(months[1][0].pk if len(months) > 1 else '')\n"
            )
            browser, tracked = self._page(pw, server, 390, 844)
            page = tracked.page
            if second_month_id:
                row = page.locator(f"#date-{second_month_id}")
                assert not row.is_visible()
                next_btn = page.locator("[data-month-step='1']")
                for _ in range(12):
                    if row.is_visible():
                        break
                    next_btn.click()
                assert row.is_visible()
                with expect_ok_response(page, "PUT", "/registration/"):
                    row.locator("input[name=name]").fill("Mobile")
                    row.locator("button[type=submit]").click()
                expect(row.locator(".event-names")).to_contain_text("Mobile", timeout=8000)
                page.locator("[data-month-step='-1']").click()
            with expect_ok_response(page, "PUT", "/type/"):
                page.locator(f"#date-{first_id} .event-type-select").select_option(label="Flex")
            with expect_ok_response(page, "PUT", "/registration/"):
                row = page.locator(f"#date-{first_id}")
                row.locator("input[name=name]").fill("Apres")
                row.locator("button[type=submit]").click()
            expect(page.locator(f"#date-{first_id} .event-names")).to_contain_text(
                "Apres", timeout=8000
            )
            tracked.assert_clean()
            browser.close()

    def test_webkit_mobile_signup_and_type(self):
        with self._server() as server, browser_tests()() as pw:
            try:
                browser = pw.webkit.launch()
            except Exception as exc:
                raise unittest.SkipTest("webkit non disponible") from exc
            event_id = self._event_id(server)
            flex_id = server.query(
                "from planning.models import GameType;print(GameType.objects.get(name='Flex').pk)"
            )
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(server.url + "/")
            page.wait_for_selector(".name-registration-form", timeout=8000)
            row = page.locator(f"#date-{event_id}")
            row.locator("input[name=name]").fill("Webkit")
            row.locator("button[type=submit]").click()
            expect(row.locator(".event-names")).to_contain_text("Webkit", timeout=10000)
            row.locator(".event-type-select").select_option(label="Flex")
            expect(row.locator(".event-type-select")).to_have_value(flex_id, timeout=10000)
            browser.close()


if __name__ == "__main__":
    unittest.main()
