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


class ExperienceLevelsE2ETests(unittest.TestCase):
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

    def _event_ids(self, server, count=2):
        out = server.query(
            "from planning.models import Event;"
            f"print('|'.join(str(e.pk) for e in Event.objects.order_by('date')[:{count}]))"
        )
        return out.split("|")

    def _page(self, pw, server, width=390):
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": 900},
            is_mobile=width < 768,
            has_touch=width < 768,
        )
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".name-registration-form", timeout=8000)
        return browser, tracked

    def _submit(self, page, event_id, name, level=None):
        row = page.locator(f"#date-{event_id}")
        form = row.locator(".name-registration-form")
        form.locator("input[name=name]").fill(name)
        if level:
            form.locator(f"input[name=experience_level][value={level}]").check()
        with expect_ok_response(page, "PUT", "/registration/"):
            form.locator("button[type=submit]").click()

    def _names(self, page, event_id):
        return page.locator(f"#date-{event_id} .event-names")

    def test_badges_legend_and_signup_at_all_widths(self):
        with self._server() as server, browser_tests()() as pw:
            event_id, second_id = self._event_ids(server)
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
                    page.goto(server.url + "/")
                    page.wait_for_selector(".name-registration-form", timeout=8000)
                    # Légende visible et champ inactif compact.
                    legend = page.locator(".level-legend")
                    expect(legend).to_be_visible()
                    expect(legend.locator(".exp-organizer")).to_be_visible()
                    expect(page.locator(f"#date-{event_id} fieldset.exp-choice")).to_be_hidden()
                    # Géométrie inactive : nom et « + » sur la même ligne.
                    form = page.locator(f"#date-{event_id} .name-registration-form")
                    name_box = form.locator("input[name=name]").bounding_box()
                    plus_box = form.locator("button[type=submit]").bounding_box()
                    form_box = form.bounding_box()
                    assert (
                        abs(
                            (name_box["y"] + name_box["height"] / 2)
                            - (plus_box["y"] + plus_box["height"] / 2)
                        )
                        <= 2
                    ), (name_box, plus_box)
                    limit = 44 if width < 768 else 40
                    assert form_box["height"] <= limit, form_box
                    # Le choix n'apparaît que lorsque le nom est saisi.
                    form.locator("input[name=name]").fill("Alice")
                    fieldset = form.locator("fieldset.exp-choice")
                    expect(fieldset).to_be_visible()
                    if width < 768:
                        # Mobile : le fieldset occupe la largeur du contenu.
                        row = page.locator(f"#date-{event_id}")
                        row_w = row.bounding_box()["width"]
                        field_w = fieldset.bounding_box()["width"]
                        assert field_w >= row_w - 32, (row_w, field_w)
                        check_no_horizontal_overflow(page)
                    if width == 320:
                        page.screenshot(path=str(SCREENSHOT_DIR / "v030-fieldset-320.png"))
                    org_radio = fieldset.locator("input[value=organizer]")
                    beg_radio = fieldset.locator("input[value=beginner]")
                    # Choix mutuellement exclusifs.
                    beg_radio.check()
                    expect(beg_radio).to_be_checked()
                    org_radio.check()
                    expect(org_radio).to_be_checked()
                    expect(beg_radio).not_to_be_checked()
                    org_radio.check()
                    with expect_ok_response(page, "PUT", "/registration/"):
                        form.locator("button[type=submit]").click()
                    names = self._names(page, event_id)
                    expect(names).to_contain_text("Alice", timeout=8000)
                    badge = names.locator(".exp-badge.exp-organizer")
                    expect(badge).to_be_visible()
                    expect(names.get_by_role("img", name="Organisateur", exact=True)).to_have_count(
                        1
                    )
                    assert badge.count() == 1
                    assert badge.locator(".exp-chevrons i").count() == 3, (
                        "Trois chevrons pour un organisateur"
                    )
                    expect(badge).to_have_attribute("title", "Organisateur")
                    assert badge.evaluate("el => el.getAttribute('aria-label')") == ("Organisateur")
                    # Deux débutants côte à côte, visibles.
                    self._submit(page, event_id, "Bob", level="beginner")
                    self._submit(page, event_id, "Carol", level="beginner")
                    expect(names).to_contain_text("Carol", timeout=8000)
                    beginners = names.locator(".exp-badge.exp-beginner")
                    expect(beginners).to_have_count(2, timeout=8000)
                    expect(beginners.first).to_have_attribute(
                        "title", "Débutant — Jamais organisé ou très peu"
                    )
                    expect(
                        names.get_by_role(
                            "img",
                            name="Débutant — Jamais organisé ou très peu",
                            exact=True,
                        )
                    ).to_have_count(2)
                    assert beginners.first.locator(".exp-chevrons i").count() == 1, (
                        "Un chevron pour un débutant"
                    )
                    # Sans choix : « Niveau à préciser ».
                    self._submit(page, event_id, "Inconnu")
                    unknown = names.locator(".exp-badge.exp-unknown")
                    expect(unknown).to_be_visible()
                    expect(unknown).to_have_attribute("title", "Niveau à préciser")
                    check_no_horizontal_overflow(page)
                    page.screenshot(path=str(SCREENSHOT_DIR / f"v030-badges-{width}.png"))
                    # Niveau mémorisé à la date suivante, choix omis.
                    self._submit(page, second_id, "Bob")
                    second = self._names(page, second_id)
                    expect(second.locator(".exp-badge.exp-beginner")).to_be_visible()
                    # Rechargement : badges persistés.
                    page.reload()
                    reloaded = self._names(page, event_id)
                    expect(reloaded).to_contain_text("Alice", timeout=8000)
                    expect(reloaded.locator(".exp-badge.exp-organizer")).to_be_visible()
                    assert reloaded.locator(".exp-badge.exp-beginner").count() == 2
                    expect(reloaded.locator(".exp-badge.exp-unknown")).to_be_visible()
                    # Note commune : 28px et espace réservé inchangés.
                    ta = page.locator(".event-note-form textarea").first
                    expect(ta).to_have_attribute("placeholder", "Note/information :")
                    page.locator("h1").click()
                    assert ta.bounding_box()["height"] <= 30
                    tracked.assert_clean()
                    context.close()
            finally:
                browser.close()

    def test_draft_survives_sync_lock_and_failure(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            browser, tracked = self._page(pw, server)
            page = tracked.page
            form = page.locator(f"#date-{event_id} .name-registration-form")
            form.locator("input[name=name]").fill("Alice")
            radio = form.locator("input[value=beginner]")
            radio.check()
            expect(radio).to_be_checked()
            # Une mise à jour distante conserve nom + choix.
            server.query(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"events.set_public_note('{event_id}', actor=Actor.bureau(),"
                " public_note='note distante', expected_version="
                f"__import__('planning.models',fromlist=['Event']).Event.objects.get(pk='{event_id}').version)"
            )
            page.evaluate("window.ath.live.refresh(true)")  # sync forcée
            page.wait_for_timeout(500)
            expect(radio).to_be_checked()
            assert form.locator("input[name=name]").input_value() == "Alice"
            # Échec réseau : le brouillon reste, message d'erreur visible.
            page.route("**/api/events/*/registration/", lambda r: r.abort())
            form.locator("button[type=submit]").click()
            msg = page.locator(f"#date-{event_id} .form-msg")
            msg.wait_for(state="visible", timeout=8000)
            assert msg.get_attribute("role") == "alert"
            expect(radio).to_be_checked()
            assert form.locator("input[name=name]").input_value() == "Alice"
            page.unroute("**/api/events/*/registration/")
            tracked.js_errors.clear()  # requête volontairement avortée
            # Date masquée : ligne conservée, radios verrouillées, brouillon visible.
            server.query(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Event\n"
                f"e = Event.objects.get(pk='{event_id}')\n"
                "events.set_event_hidden(e.pk, actor=Actor.bureau(), is_hidden=True, "
                f"expected_version=e.version)"
            )
            page.evaluate("window.ath.live.refresh(true)")
            page.wait_for_timeout(500)
            expect(page.locator(f"#date-{event_id}")).to_be_visible()
            expect(radio).to_be_checked()
            expect(radio).to_be_disabled()
            assert form.locator("input[name=name]").input_value() == "Alice"
            # Date rouverte : formulaire déverrouillé, brouillon intact.
            server.query(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Event\n"
                f"e = Event.objects.get(pk='{event_id}')\n"
                "events.set_event_hidden(e.pk, actor=Actor.bureau(), is_hidden=False, "
                f"expected_version=e.version)"
            )
            page.evaluate("window.ath.live.refresh(true)")
            page.wait_for_timeout(500)
            expect(radio).to_be_checked()
            expect(radio).to_be_enabled()
            # Inscription réussie : champ et choix réinitialisés.
            with expect_ok_response(page, "PUT", "/registration/"):
                form.locator("button[type=submit]").click()
            expect(self._names(page, event_id)).to_contain_text("Alice", timeout=8000)
            assert form.locator("input[name=name]").input_value() == ""
            expect(form.locator("fieldset.exp-choice")).to_be_hidden()
            expect(self._names(page, event_id).locator(".exp-badge.exp-beginner")).to_be_visible()
            tracked.assert_clean()
            browser.close()

    def test_grade_only_update_reaches_other_client(self):
        with self._server() as server, browser_tests()() as pw:
            event_id, second_id = self._event_ids(server)
            browser = pw.chromium.launch()
            try:
                ctx_a = browser.new_context(viewport={"width": 390, "height": 900})
                tracked_a = TrackedPage(ctx_a.new_page())
                tracked_a.page.goto(server.url + "/")
                tracked_a.page.wait_for_selector(".name-registration-form")
                ctx_b = browser.new_context(viewport={"width": 390, "height": 900})
                tracked_b = TrackedPage(ctx_b.new_page())
                tracked_b.page.goto(server.url + "/")
                tracked_b.page.wait_for_selector(".name-registration-form")
                self._submit(tracked_a.page, event_id, "Alice", level="organizer")
                expect(
                    self._names(tracked_b.page, event_id).locator(".exp-badge.exp-organizer")
                ).to_be_visible(timeout=8000)
                # Alice se requalifie « débutant » sur une autre date : la
                # propagation met à jour le badge du premier événement chez B.
                self._submit(tracked_a.page, second_id, "Alice", level="beginner")
                tracked_b.page.evaluate("window.ath.live.refresh(true)")
                expect(
                    self._names(tracked_b.page, event_id).locator(".exp-badge.exp-beginner")
                ).to_be_visible(timeout=8000)
                tracked_a.assert_clean()
                tracked_b.assert_clean()
            finally:
                browser.close()

    def test_editing_name_clears_chosen_level(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            browser, tracked = self._page(pw, server)
            page = tracked.page
            form = page.locator(f"#date-{event_id} .name-registration-form")
            form.locator("input[name=name]").fill("Alice")
            radio = form.locator("input[value=beginner]")
            radio.check()
            expect(radio).to_be_checked()
            # Modifier le nom efface le choix fait pour le nom précédent.
            form.locator("input[name=name]").fill("Bob")
            expect(radio).not_to_be_checked()
            expect(form.locator("input[value=organizer]")).not_to_be_checked()
            expect(form.locator("fieldset.exp-choice")).to_be_visible()
            with expect_ok_response(page, "PUT", "/registration/"):
                form.locator("button[type=submit]").click()
            names = self._names(page, event_id)
            expect(names).to_contain_text("Bob", timeout=8000)
            assert names.locator(".exp-badge.exp-beginner").count() == 0
            expect(names.locator(".exp-badge.exp-unknown")).to_be_visible()
            tracked.assert_clean()
            browser.close()

    def test_bureau_correction_from_event_form(self):
        with self._server() as server, browser_tests()() as pw:
            server.seed(SEED_BUREAU)
            event_id = self._event_ids(server, 1)[0]
            organizer_id = server.query(
                "from planning.services import organizers\n"
                "from planning.services.actors import Actor\n"
                "o = organizers.get_or_create_organizer('Alice', actor=Actor.bureau())\n"
                "print(o.pk)"
            )
            browser, tracked = self._page(pw, server)
            page = tracked.page
            self._submit(page, event_id, "Alice")
            expect(self._names(page, event_id).locator(".exp-badge.exp-unknown")).to_be_visible()
            bureau = browser.new_context().new_page()
            bureau.goto(server.url + "/bureau/")
            bureau.fill("input[type=password]", BUREAU_PASSWORD)
            bureau.locator("button[type=submit]").click()
            bureau.wait_for_url("**/bureau/")
            bureau.goto(server.url + f"/bureau/dates/{event_id}/")
            bureau.locator("details.card > summary").click()
            bureau.select_option("select[name=organizer]", organizer_id)
            bureau.select_option("select[name=state]", "active")
            bureau.select_option("select[name=experience_level]", "beginner")
            bureau.locator("button[name=action][value=registration]").click()
            bureau.wait_for_load_state("networkidle")
            expect(bureau.locator(".event-list .exp-badge.exp-beginner")).to_be_visible()
            page.reload()
            expect(self._names(page, event_id).locator(".exp-badge.exp-beginner")).to_be_visible()
            tracked.assert_clean()
            browser.close()
