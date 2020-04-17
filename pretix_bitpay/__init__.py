from django.apps import AppConfig
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _


class BitpayApp(AppConfig):
    name = 'pretix_bitpay'
    verbose_name = _("BitPay")

    class PretixPluginMeta:
        name = _("BitPay")
        author = "Raphael Michel"
        category = 'PAYMENT'
        version = '1.4.1'
        description = _("This plugin allows you to receive Bitcoin payments " +
                        "via BitPay-compatible payment providers.")

    def ready(self):
        from . import signals   # NOQA

    @cached_property
    def compatibility_errors(self):
        errs = []
        try:
            import btcpay  # NOQA
        except ImportError:
            errs.append("Python package 'btcpay' is not installed.")
        return errs


default_app_config = 'pretix_bitpay.BitpayApp'
