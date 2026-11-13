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


class MonthNavigationTests(unittest.TestCase):
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

    def _month_event_id(self, server, month_index):
        return server.query(
            "from planning.models import Event\n"
            "from itertools import groupby\n"
            "events = list(Event.objects.order_by('date'))\n"
            "months = [list(g) for _, g in groupby(events, "
            "key=lambda e: (e.date.year, e.date.month))]\n"
            f"print(months[{month_index}][0].pk if len(months) > {month_index} else '')\n"
        )

    def _event_id(self, server):
        return server.query(
            "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
        )

    def _page(self, pw, server, width=390, touch=True, path="/", height=844):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch)
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + path)
        tracked.page.wait_for_selector("section.month-group:visible", timeout=8000)
        return browser, context, tracked

    def _visible_months(self, page):
        return page.locator("section.month-group:visible")

    def _active_key(self, page):
        return page.evaluate("window.ath.months.activeKey()")

    def _month_keys(self, page):
        return page.evaluate(
            "[...document.querySelectorAll('section.month-group')].map(g => g.dataset.month).sort()"
        )

    def _next_to(self, page, event_id):
        row = page.locator(f"#date-{event_id}")
        for _ in range(12):
            if row.is_visible():
                return True
            target_key = row.evaluate("el => el.closest('.month-group').dataset.month")
            active = self._active_key(page)
            btn = page.locator(
                "[data-month-step='-1']" if target_key < active else "[data-month-step='1']"
            )
            if btn.is_disabled():
                return False
            btn.click()
        return row.is_visible()

    def _pointer_swipe(self, page, x0, y0, x1, y1, target=None, cancel=False):
        target_js = target or "document.getElementById('event-list')"
        up_js = (
            "el.dispatchEvent(new PointerEvent('pointerup', {pointerId: 1, "
            "pointerType: 'touch', "
            f"clientX: {x1}, clientY: {y1}, bubbles: true}}));"
        )
        cancel_js = (
            "el.dispatchEvent(new PointerEvent('pointercancel', "
            "{pointerId: 1, pointerType: 'touch', bubbles: true}));"
            if cancel
            else up_js
        )
        page.evaluate(
            f"""() => {{
            const el = {target_js};
            el.dispatchEvent(new PointerEvent('pointerdown', {{
              pointerId: 1, pointerType: 'touch', clientX: {x0}, clientY: {y0},
              button: 0, isPrimary: true, bubbles: true}}));
            {cancel_js}
        }}"""
        )

    def test_arrows_single_visible_boundaries_keyboard(self):
        with self._server() as server, browser_tests()() as pw:
            browser, context, tracked = self._page(pw, server)
            page = tracked.page
            assert self._visible_months(page).count() == 1
            keys = self._month_keys(page)
            assert len(keys) > 1
            first = self._active_key(page)
            assert first == keys[0]
            title = page.locator("#month-title").text_content().strip()
            assert title
            prev_btn = page.locator("[data-month-step='-1']")
            next_btn = page.locator("[data-month-step='1']")
            assert prev_btn.is_disabled()
            next_btn.click()
            assert self._visible_months(page).count() == 1
            assert self._active_key(page) == keys[1]
            assert page.locator("#month-title").text_content().strip() != title
            assert prev_btn.is_enabled()
            for _ in range(len(keys)):
                if next_btn.is_disabled():
                    break
                next_btn.click()
            assert self._active_key(page) == keys[-1]
            assert next_btn.is_disabled()
            prev_btn.click()
            assert self._active_key(page) == keys[-2]
            prev_btn.focus()
            page.keyboard.press("ArrowLeft")
            assert self._active_key(page) == keys[-3]
            page.keyboard.press("ArrowRight")
            assert self._active_key(page) == keys[-2]
            event_id = self._event_id(server)
            assert self._next_to(page, event_id)
            active = self._active_key(page)
            page.locator(f"#date-{event_id} input[name=name]").fill("x")
            page.keyboard.press("ArrowRight")
            assert self._active_key(page) == active
            tracked.assert_clean()
            browser.close()

    def test_cdp_touch_swipe_navigates(self):
        with self._server() as server, browser_tests()() as pw:
            browser, context, tracked = self._page(pw, server)
            page = tracked.page
            keys = self._month_keys(page)
            box = page.locator(".event-date-cell").first.bounding_box()
            cdp = context.new_cdp_session(page)
            x0, y0 = box["x"] + box["width"] - 12, box["y"] + 5

            def touch(kind, points):
                cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

            touch("touchStart", [{"x": x0, "y": y0, "id": 1}])
            touch("touchMove", [{"x": x0 - 140, "y": y0 + 10, "id": 1}])
            touch("touchEnd", [])
            assert self._active_key(page) == keys[1]
            touch("touchStart", [{"x": 60, "y": y0, "id": 1}])
            touch("touchMove", [{"x": 60 + 140, "y": y0 + 10, "id": 1}])
            touch("touchEnd", [])
            assert self._active_key(page) == keys[0]
            tracked.assert_clean()
            browser.close()

    def test_gestures_rejected(self):
        with self._server() as server, browser_tests()() as pw:
            browser, context, tracked = self._page(pw, server, height=480)
            page = tracked.page
            keys = self._month_keys(page)
            self._pointer_swipe(page, 200, 600, 200, 300)
            assert self._active_key(page) == keys[0]
            scroll0 = page.evaluate("window.scrollY")
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(100)
            assert page.evaluate("window.scrollY") > scroll0
            assert self._active_key(page) == keys[0]
            self._pointer_swipe(page, 200, 400, 240, 400)
            assert self._active_key(page) == keys[0]
            self._pointer_swipe(page, 200, 400, 100, 480)
            assert self._active_key(page) == keys[0]
            event_id = self._event_id(server)
            self._pointer_swipe(
                page,
                200,
                400,
                60,
                400,
                target=f"document.querySelector('#date-{event_id} input[name=name]')",
            )
            assert self._active_key(page) == keys[0]
            self._pointer_swipe(page, 200, 400, 60, 400, cancel=True)
            assert self._active_key(page) == keys[0]
            tracked.assert_clean()
            browser.close()

    def test_draft_persists_across_months(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            browser, context, tracked = self._page(pw, server)
            page = tracked.page
            input_el = page.locator(f"#date-{event_id} input[name=name]")
            input_el.fill("Brouillon")
            page.locator("[data-month-step='1']").click()
            page.locator("[data-month-step='-1']").click()
            assert input_el.input_value() == "Brouillon"
            tracked.assert_clean()
            browser.close()

    def test_signup_and_type_stay_on_month(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._month_event_id(server, 1)
            browser, context, tracked = self._page(pw, server)
            page = tracked.page
            assert self._next_to(page, event_id)
            active = self._active_key(page)
            row = page.locator(f"#date-{event_id}")
            with expect_ok_response(page, "PUT", "/registration/"):
                row.locator("input[name=name]").fill("Loin")
                row.locator("button[type=submit]").click()
            expect(row.locator(".event-names")).to_contain_text("Loin", timeout=8000)
            assert self._active_key(page) == active
            flex_id = server.query(
                "from planning.models import GameType;print(GameType.objects.get(name='Flex').pk)"
            )
            with expect_ok_response(page, "PUT", "/type/"):
                row.locator(".event-type-select").select_option(label="Flex")
            expect(row.locator(".event-type-select")).to_have_value(flex_id, timeout=8000)
            assert self._active_key(page) == active
            tracked.assert_clean()
            browser.close()

    def test_remote_update_keeps_selected_month(self):
        with self._server() as server, browser_tests()() as pw:
            first_id = self._event_id(server)
            other_id = self._month_event_id(server, 1)
            browser = pw.chromium.launch()
            ctx_a = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx_b = browser.new_context(viewport={"width": 390, "height": 844})
            page_a = ctx_a.new_page()
            page_b = ctx_b.new_page()
            page_a.goto(server.url + "/")
            page_b.goto(server.url + "/")
            page_b.wait_for_selector("section.month-group", timeout=8000)
            assert self._next_to(page_b, other_id)
            active = self._active_key(page_b)
            row_a = page_a.locator(f"#date-{first_id}")
            with expect_ok_response(page_a, "PUT", "/registration/"):
                row_a.locator("input[name=name]").fill("Alice")
                row_a.locator("button[type=submit]").click()
            expect(page_b.locator(f"#date-{first_id} .event-names")).to_contain_text(
                "Alice", timeout=8000
            )
            assert self._active_key(page_b) == active
            assert self._visible_months(page_b).count() == 1
            browser.close()

    def test_month_removed_selects_nearest(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._month_event_id(server, 1)
            keys_before = server.query(
                "from planning.models import Event\n"
                "from itertools import groupby\n"
                "events = list(Event.objects.order_by('date'))\n"
                "months = [list(g) for _, g in groupby(events, "
                "key=lambda e: (e.date.year, e.date.month))]\n"
                "print('|'.join(f'{m[0].date.year}-{m[0].date.month:02d}' for m in months))\n"
            ).split("|")
            browser, context, tracked = self._page(pw, server)
            page = tracked.page
            assert self._next_to(page, event_id)
            removed_key = keys_before[1]
            assert self._active_key(page) == removed_key
            expected_key = keys_before[2] if len(keys_before) > 2 else keys_before[0]
            server.seed(
                "from planning.models import Event\n"
                "import datetime\n"
                f"target = Event.objects.get(pk='{event_id}')\n"
                "events = list(Event.objects.filter("
                "date__year=target.date.year, date__month=target.date.month))\n"
                "for e in events:\n"
                "    e.date = e.date - datetime.timedelta(days=365)\n"
                "Event.objects.bulk_update(events, ['date'])\n"
                "p = target.period\n"
                "p.revision += 1\n"
                "p.save(update_fields=['revision'])\n"
            )
            for _ in range(30):
                if self._active_key(page) == expected_key:
                    break
                page.wait_for_timeout(300)
            assert self._active_key(page) == expected_key
            assert self._visible_months(page).count() == 1
            tracked.assert_clean()
            browser.close()

    def test_deep_link_opens_month(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._month_event_id(server, 1)
            browser, context, tracked = self._page(pw, server, path=f"/#date-{event_id}")
            page = tracked.page
            expect(page.locator(f"#date-{event_id}")).to_be_visible(timeout=8000)
            assert self._month_event_id(server, 1)
            assert page.locator(f"section.month-group:has(#date-{event_id})").is_visible()
            tracked.assert_clean()
            browser.close()

    def test_empty_period_nav_hidden_then_appears(self):
        with self._server() as server, browser_tests()() as pw:
            server.seed(
                "from planning.models import Event\n"
                "import datetime\n"
                "events = list(Event.objects.all())\n"
                "for e in events:\n"
                "    e.date = e.date - datetime.timedelta(days=365)\n"
                "Event.objects.bulk_update(events, ['date'])\n"
                "p = events[0].period\n"
                "p.revision += 1\n"
                "p.save(update_fields=['revision'])\n"
            )
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(server.url + "/")
            page.wait_for_selector(".planning-empty", timeout=8000)
            assert page.locator(".month-navigation").is_hidden()
            server.seed(
                "from planning.models import Event\n"
                "import datetime\n"
                "e = Event.objects.order_by('date').first()\n"
                "e.date = datetime.date.today() + datetime.timedelta(days=10)\n"
                "e.save(update_fields=['date'])\n"
                "p = e.period\n"
                "p.revision += 1\n"
                "p.save(update_fields=['revision'])\n"
            )
            page.wait_for_selector("section.month-group", timeout=10000)
            assert page.locator(".month-navigation").is_visible()
            assert self._visible_months(page).count() == 1
            browser.close()

    def test_viewports_screenshots(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_id(server)
            server.seed(
                "from planning.services import organizers, registrations, events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import GameType\n"
                "for n in ('Alice', 'Bob'):\n"
                "    o = organizers.get_or_create_organizer(n)\n"
                f"    registrations.set_registration('{event_id}', o.pk,"
                " 'active', actor=Actor.public(o))\n"
                f"events.update_event('{event_id}', actor=Actor.bureau(),"
                " game_type_id=str(GameType.objects.get(name='Flex').pk))\n"
            )
            for width in (320, 390, 430, 1440):
                browser, context, tracked = self._page(pw, server, width=width, touch=False)
                page = tracked.page
                check_no_horizontal_overflow(page)
                nav_box = page.locator(".month-navigation").bounding_box()
                assert nav_box["width"] <= width, (width, nav_box)
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"monthly-swipe-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def test_webkit_touch_navigation_and_signup(self):
        with self._server() as server, browser_tests()() as pw:
            try:
                browser = pw.webkit.launch()
            except Exception as exc:
                raise unittest.SkipTest("webkit non disponible") from exc
            event_id = self._month_event_id(server, 1)
            keys = server.query(
                "from planning.models import Event\n"
                "from itertools import groupby\n"
                "events = list(Event.objects.order_by('date'))\n"
                "months = [list(g) for _, g in groupby(events, "
                "key=lambda e: (e.date.year, e.date.month))]\n"
                "print('|'.join(f'{m[0].date.year}-{m[0].date.month:02d}' for m in months))\n"
            ).split("|")
            context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
            page = context.new_page()
            page.goto(server.url + "/")
            page.wait_for_selector("section.month-group", timeout=8000)
            page.locator("[data-month-step='1']").click()
            assert page.evaluate("window.ath.months.activeKey()") == keys[1]
            row = page.locator(f"#date-{event_id}")
            row.locator("input[name=name]").fill("Webkit")
            row.locator("button[type=submit]").click()
            expect(row.locator(".event-names")).to_contain_text("Webkit", timeout=10000)
            assert page.evaluate("window.ath.months.activeKey()") == keys[1]
            browser.close()


if __name__ == "__main__":
    unittest.main()
