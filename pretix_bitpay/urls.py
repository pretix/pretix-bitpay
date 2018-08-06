from django.conf.urls import url, include

from pretix.multidomain import event_url
from .views import auth_start, auth_disconnect, webhook, ReturnView, redirect_view

event_patterns = [
    url(r'^bitpay/', include([
        event_url(r'^webhook/$', webhook, name='webhook', require_live=False),
        event_url(r'^redirect/$', redirect_view, name='redirect', require_live=False),
        url(r'^return/(?P<order>[^/]+)/(?P<hash>[^/]+)/(?P<payment>[^/]+)/$', ReturnView.as_view(), name='return'),
    ])),
]
urlpatterns = [
    url(r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/bitpay/connect/',
        auth_start, name='auth.start'),
    url(r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/bitpay/disconnect/',
        auth_disconnect, name='auth.disconnect'),
]
