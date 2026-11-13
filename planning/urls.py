from django.urls import path

from . import api_public, views_bureau, views_public, views_reports

urlpatterns = [
    path("", views_public.index, name="index"),
    path("dates/<uuid:event_id>/", views_public.event_detail, name="event_detail"),
    path(
        "mes-inscriptions/",
        views_public.my_registrations,
        name="my_registrations",
    ),
    path("healthz", views_public.healthz, name="healthz"),
    path("robots.txt", views_public.robots_txt, name="robots_txt"),
    path("api/periods/", api_public.periods_list, name="api_periods"),
    path(
        "api/periods/<uuid:period_id>/planning/",
        api_public.period_planning,
        name="api_planning",
    ),
    path(
        "api/periods/<uuid:period_id>/revision/",
        api_public.period_revision,
        name="api_revision",
    ),
    path(
        "api/periods/<uuid:period_id>/live/",
        api_public.period_live,
        name="api_period_live",
    ),
    path("api/organizers/", api_public.organizers_list, name="api_organizers"),
    path(
        "api/organizers/create/",
        api_public.organizer_create,
        name="api_organizer_create",
    ),
    path(
        "api/organizers/<uuid:organizer_id>/registrations/",
        api_public.organizer_registrations,
        name="api_organizer_registrations",
    ),
    path(
        "api/events/<uuid:event_id>/registration/",
        api_public.event_registration,
        name="api_event_registration",
    ),
    path(
        "api/events/<uuid:event_id>/type/",
        api_public.event_type,
        name="api_event_type",
    ),
    path(
        "api/registrations/batch/",
        api_public.registrations_batch,
        name="api_registrations_batch",
    ),
    path("bureau/connexion/", views_bureau.bureau_login, name="bureau_login"),
    path("bureau/deconnexion/", views_bureau.bureau_logout, name="bureau_logout"),
    path("bureau/", views_bureau.dashboard, name="bureau_dashboard"),
    path(
        "bureau/periodes/nouvelle/",
        views_bureau.period_create,
        name="bureau_period_create",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/",
        views_bureau.period_detail,
        name="bureau_period_detail",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/cloture/",
        views_bureau.period_finalize,
        name="bureau_period_finalize",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/nettoyage/",
        views_bureau.period_cleanup,
        name="bureau_period_cleanup",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/supprimer/",
        views_bureau.period_delete,
        name="bureau_period_delete",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/dates/nouvelle/",
        views_bureau.event_create,
        name="bureau_event_create",
    ),
    path(
        "bureau/periodes/<uuid:period_id>/dates/groupe/",
        views_bureau.events_batch,
        name="bureau_events_batch",
    ),
    path(
        "bureau/dates/<uuid:event_id>/",
        views_bureau.event_edit,
        name="bureau_event_edit",
    ),
    path("bureau/pseudos/", views_bureau.organizers_list, name="bureau_organizers"),
    path("bureau/types/", views_bureau.types_list, name="bureau_types"),
    path(
        "bureau/types/<uuid:type_id>/supprimer/",
        views_bureau.type_delete,
        name="bureau_type_delete",
    ),
    path("bureau/journal/", views_bureau.journal, name="bureau_journal"),
    path(
        "bureau/exports/periode/<uuid:period_id>.csv",
        views_bureau.export_period_csv,
        name="bureau_export_csv",
    ),
    path(
        "bureau/exports/donnees.json",
        views_bureau.export_business_json,
        name="bureau_export_json",
    ),
    path(
        "bureau/exports/periode/<uuid:period_id>.xlsx",
        views_reports.export_xlsx,
        name="bureau_export_xlsx",
    ),
    path(
        "bureau/exports/periode/<uuid:period_id>/imprimer/",
        views_reports.printable,
        name="bureau_export_print",
    ),
    path(
        "bureau/mot-de-passe/",
        views_bureau.password_change,
        name="bureau_password",
    ),
    path(
        "bureau/reglages/",
        views_bureau.site_settings,
        name="bureau_settings",
    ),
]
