import time
import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    LiveServer,
    TrackedPage,
    browser_tests,
    expect_ok_response,
    seed_period_script,
)


class LiveSyncTests(unittest.TestCase):
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

    def _second_event_id(self, server):
        return server.query(
            "from planning.models import Event;print(Event.objects.order_by('date')[1].pk)"
        )

    def _flex_id(self, server):
        return server.query(
            "from planning.models import GameType;print(GameType.objects.get(name='Flex').pk)"
        )

    def _roltac_id(self, server):
        return server.query(
            "from planning.models import GameType;print(GameType.objects.get(name='Roltac').pk)"
        )

    def _open_pair(self, pw, server):
        browser = pw.chromium.launch()
        ctx_a = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx_b = browser.new_context(viewport={"width": 390, "height": 844})
        page_a = TrackedPage(ctx_a.new_page()).page
        page_b = TrackedPage(ctx_b.new_page()).page
        page_a.goto(server.url + "/")
        page_b.goto(server.url + "/")
        page_a.wait_for_selector(".name-registration-form", timeout=8000)
        page_b.wait_for_selector(".name-registration-form", timeout=8000)
        navs = {"a": 0, "b": 0}
        page_a.on("framenavigated", lambda f: navs.__setitem__("a", navs["a"] + 1))
        page_b.on("framenavigated", lambda f: navs.__setitem__("b", navs["b"] + 1))
        return browser, page_a, page_b, navs

    def _submit(self, page, event_id, name):
        row = page.locator(f"#date-{event_id}")
        row.locator(".name-registration-form input[name=name]").fill(name)
        row.locator(".name-registration-form button[type=submit]").click()

    def _names(self, page, event_id):
        return page.locator(f"#date-{event_id} .event-names")

    def _select(self, page, event_id):
        return page.locator(f"#date-{event_id} .event-type-select")

    def test_remote_signup_syncs_preserving_draft_focus_scroll(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            other_id = self._second_event_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            input_b = page_b.locator(f"#date-{other_id} .name-registration-form input[name=name]")
            input_b.fill("Bo")
            input_b.focus()
            page_b.mouse.wheel(0, 80)
            page_b.wait_for_timeout(150)
            scroll_y = page_b.evaluate("window.scrollY")
            row_top = page_b.evaluate(
                f"document.getElementById('date-{event_id}').getBoundingClientRect().top"
            )
            active_key = page_b.evaluate("window.ath.months.activeKey()")
            with expect_ok_response(page_a, "PUT", "/registration/"):
                self._submit(page_a, event_id, "Alice")
            expect(self._names(page_b, event_id)).to_contain_text("Alice", timeout=8000)
            assert input_b.input_value() == "Bo"
            assert page_b.evaluate(
                "document.activeElement === document.querySelector("
                f"'#date-{other_id} input[name=name]')"
            )
            assert page_b.evaluate("document.activeElement.selectionStart") == 2
            assert page_b.evaluate("window.ath.months.activeKey()") == active_key
            assert abs(page_b.evaluate("window.scrollY") - scroll_y) <= 2
            assert (
                abs(
                    page_b.evaluate(
                        f"document.getElementById('date-{event_id}').getBoundingClientRect().top"
                    )
                    - row_top
                )
                <= 2
            )
            assert navs["a"] == 0 and navs["b"] == 0
            browser.close()

    def test_remote_type_syncs_and_signup_still_works(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            flex_id = self._flex_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            with expect_ok_response(page_a, "PUT", "/type/"):
                self._select(page_a, event_id).select_option(label="Flex")
            expect(self._select(page_b, event_id)).to_have_value(flex_id, timeout=8000)
            with expect_ok_response(page_b, "PUT", "/registration/"):
                self._submit(page_b, event_id, "Bob")
            expect(self._names(page_a, event_id)).to_contain_text("Bob", timeout=8000)
            browser.close()

    def test_focused_select_defers_remote_change_until_blur(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            roltac_id = self._roltac_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            select_b = self._select(page_b, event_id)
            select_b.focus()
            before = select_b.input_value()
            with expect_ok_response(page_a, "PUT", "/type/"):
                self._select(page_a, event_id).select_option(label="Roltac")
            time.sleep(3)
            assert select_b.input_value() == before
            page_b.locator("h1#planning-title").click()
            expect(select_b).to_have_value(roltac_id, timeout=8000)
            browser.close()

    def test_concurrent_signups_and_duplicate_idempotent(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            self._submit(page_a, event_id, "Alice")
            self._submit(page_b, event_id, "Bob")
            expect(self._names(page_a, event_id)).to_contain_text("Bob", timeout=8000)
            expect(self._names(page_b, event_id)).to_contain_text("Alice", timeout=8000)
            count = server.query(
                "from planning.models import Registration;"
                f"print(Registration.objects.filter(event_id='{event_id}', state='active').count())"
            )
            assert int(count) == 2
            self._submit(page_a, event_id, "Alice")
            time.sleep(1)
            count = server.query(
                "from planning.models import Registration;"
                f"print(Registration.objects.filter(event_id='{event_id}', state='active').count())"
            )
            assert int(count) == 2
            browser.close()

    def test_competing_type_writes_loser_restored(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            flex_id = self._flex_id(server)
            roltac_id = self._roltac_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            version = page_a.evaluate(f"document.getElementById('date-{event_id}').dataset.version")
            assert (
                page_b.evaluate(f"document.getElementById('date-{event_id}').dataset.version")
                == version
            )
            statuses = page_a.evaluate(
                """async ([eventId, flexId, roltacId, version]) => {
                const token = document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/)[1];
                const send = (gt) => fetch(`/api/events/${eventId}/type/`, {
                  method: 'PUT',
                  headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                  body: JSON.stringify({game_type_id: gt,
                    expected_event_version: parseInt(version, 10)}),
                }).then(r => r.status);
                return Promise.all([send(flexId), send(roltacId)]);
            }""",
                [event_id, flex_id, roltac_id, version],
            )
            assert sorted(statuses) == [200, 409], statuses
            winner = server.query(
                "from planning.models import Event;"
                f"e=Event.objects.get(pk='{event_id}');"
                "print(str(e.game_type_id or ''))"
            )
            for page in (page_a, page_b):
                page.evaluate("window.ath.live.refresh(true)")
                expect(self._select(page, event_id)).to_have_value(winner, timeout=8000)
            browser.close()

    def test_legacy_closure_flag_keeps_draft_interactive(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, page_a, page_b, navs = self._open_pair(pw, server)
            row_a = page_a.locator(f"#date-{event_id}")
            row_b = page_b.locator(f"#date-{event_id}")
            input_b = row_b.locator(".name-registration-form input[name=name]")
            input_b.fill("Draft")
            for flag in ("False", "True"):
                server.seed(
                    "from planning.models import Event\n"
                    f"e = Event.objects.get(pk='{event_id}')\n"
                    f"e.registrations_open = {flag}\n"
                    "e.save(update_fields=['registrations_open'])\n"
                    "p = e.period\n"
                    "p.revision += 1\n"
                    "p.save(update_fields=['revision'])\n"
                )
                for page in (page_a, page_b):
                    page.evaluate("window.ath.live.refresh(true)")
                for row in (row_a, row_b):
                    assert row.get_attribute("data-open") == "true"
                    expect(row.locator(".event-lock-status")).to_have_count(0)
                    assert row.locator(".name-registration-form button[type=submit]").is_enabled()
                assert input_b.input_value() == "Draft"
                assert input_b.is_enabled()
            assert navs["a"] == 0 and navs["b"] == 0
            browser.close()

    def test_no_overlapping_live_requests(self):
        with self._server() as server, browser_tests()() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            inflight = {"n": 0, "max": 0}

            def on_request(req):
                if "/live/" in req.url:
                    inflight["n"] += 1
                    inflight["max"] = max(inflight["max"], inflight["n"])

            def on_finished(req):
                if "/live/" in req.url:
                    inflight["n"] -= 1

            page.on("request", on_request)
            page.on("requestfinished", on_finished)
            page.on("requestfailed", on_finished)
            page.goto(server.url + "/")
            page.wait_for_selector(".name-registration-form", timeout=8000)
            page.wait_for_timeout(5500)
            assert inflight["max"] == 1
            browser.close()

    def _month_event_id(self, server, month_index):
        return server.query(
            "from planning.models import Event\n"
            "from itertools import groupby\n"
            "events = list(Event.objects.order_by('date'))\n"
            "months = [list(g) for _, g in groupby(events, "
            "key=lambda e: (e.date.year, e.date.month))]\n"
            f"print(months[{month_index}][0].pk if len(months) > {month_index} else '')\n"
        )

    def _page(self, pw, server, width=390):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 844})
        page = context.new_page()
        page.goto(server.url + "/")
        return browser, page

    def test_aborted_live_get_keeps_controls_and_recovers(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            browser, page = self._page(pw, server)
            page_errors = []
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            aborts = {"live": True, "put": True}

            def handler(route, request):
                if "/live/" in request.url and aborts["live"]:
                    route.abort()
                elif request.method == "PUT" and "/registration/" in request.url and aborts["put"]:
                    aborts["put"] = False
                    route.abort()
                else:
                    route.continue_()

            page.route("**/api/**", handler)
            page.wait_for_selector(".name-registration-form", timeout=8000)
            row = page.locator(f"#date-{event_id}")
            input_el = row.locator("input[name=name]")
            button = row.locator(".name-registration-form button[type=submit]")
            self._submit(page, event_id, "Bob")
            expect(row.locator(".form-msg")).to_be_visible(timeout=8000)
            assert input_el.is_enabled()
            assert input_el.input_value() == "Bob"
            assert button.is_enabled()
            with expect_ok_response(page, "PUT", "/registration/"):
                self._submit(page, event_id, "Bob")
            expect(page.locator("#toast")).to_contain_text("Ajouté", timeout=8000)
            assert input_el.is_enabled() and button.is_enabled()
            count = server.query(
                "from planning.models import Registration;"
                f"print(Registration.objects.filter(event_id='{event_id}', state='active').count())"
            )
            assert int(count) == 1
            page.unroute("**/api/**")
            aborts["live"] = False
            expect(row.locator(".event-names")).to_contain_text("Bob", timeout=10000)
            assert page_errors == [], page_errors
            browser.close()

    def test_stale_snapshot_never_reverts_type(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._first_event_id(server)
            flex_id = self._flex_id(server)
            period_id = server.query(
                "from planning.models import Period;print(Period.objects.first().pk)"
            )
            browser, page = self._page(pw, server)
            page.wait_for_selector(".name-registration-form", timeout=8000)
            old = page.request.get(server.url + f"/api/periods/{period_id}/live/").json()
            held = []

            def handler(route, request):
                if "/live/" in request.url and not held:
                    held.append(route)
                else:
                    route.continue_()

            page.route("**/live/**", handler)
            page.wait_for_timeout(3000)
            assert held, "no live request captured"
            select = self._select(page, event_id)
            with expect_ok_response(page, "PUT", "/type/"):
                select.select_option(label="Flex")
            import json

            held[0].fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(old),
            )
            expect(select).to_have_value(flex_id, timeout=8000)
            page.wait_for_timeout(2500)
            assert select.input_value() == flex_id
            new_version = server.query(
                "from planning.models import Event;"
                f"print(Event.objects.get(pk='{event_id}').version)"
            )
            assert (
                page.evaluate(f"document.getElementById('date-{event_id}').dataset.version")
                == new_version
            )
            browser.close()

    def test_visibility_changes_bounded_requests(self):
        with self._server() as server, browser_tests()() as pw:
            browser, page = self._page(pw, server)
            pending = []
            count = {"n": 0}

            def handler(route, request):
                count["n"] += 1
                pending.append(route)

            page.route("**/live/**", handler)
            page.wait_for_selector(".name-registration-form", timeout=8000)
            page.wait_for_timeout(2800)
            assert len(pending) <= 1, len(pending)
            for _ in range(4):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(500)
            held = list(pending)
            pending.clear()
            for route in held:
                route.continue_()
            page.wait_for_timeout(1500)
            assert len(pending) <= 1, len(pending)
            for route in list(pending):
                route.continue_()
            assert count["n"] <= 6, count["n"]
            browser.close()

    def test_empty_period_day_appears_and_disappears(self):
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
            browser, page = self._page(pw, server)
            page.wait_for_selector(".planning-empty", timeout=8000)
            assert page.locator("li.event-card").count() == 0
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
            page.wait_for_selector("li.event-card", timeout=10000)
            expect(page.locator(".planning-empty")).to_have_count(0)
            server.seed(
                "from planning.models import Event\n"
                "import datetime\n"
                "e = Event.objects.filter(date__gte=datetime.date.today()).get()\n"
                "e.date = datetime.date.today() - datetime.timedelta(days=10)\n"
                "e.save(update_fields=['date'])\n"
                "p = e.period\n"
                "p.revision += 1\n"
                "p.save(update_fields=['revision'])\n"
            )
            page.wait_for_selector(".planning-empty", timeout=10000)
            assert page.locator("li.event-card").count() == 0
            browser.close()

    def test_group_removed_keeps_disabled_draft_then_cleanup(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._month_event_id(server, 1)
            browser, page = self._page(pw, server)
            page.wait_for_selector(".name-registration-form", timeout=8000)
            group = page.locator(f"section.month-group:has(#date-{event_id})")
            next_btn = page.locator("[data-month-step='1']")
            for _ in range(12):
                if group.is_visible():
                    break
                next_btn.click()
            assert group.is_visible()
            input_el = page.locator(f"#date-{event_id} input[name=name]")
            input_el.fill("Gardé")
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
            row = page.locator(f"#date-{event_id}")
            expect(row.locator(".form-msg")).to_contain_text("Date retirée", timeout=10000)
            assert row.locator(".name-registration-form button[type=submit]").is_disabled()
            assert input_el.input_value() == "Gardé"
            input_el.fill("")
            input_el.blur()
            expect(row).to_have_count(0, timeout=8000)
            browser.close()
