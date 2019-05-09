from django.apps import AppConfig
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _


class BitpayApp(AppConfig):
    name = 'pretix_bitpay'
    verbose_name = _("BitPay")

    class PretixPluginMeta:
        name = _("BitPay")
        author = "Raphael Michel"
        version = '1.1.0'
        description = _("This plugin allows you to receive Bitcoin payments " +
                        "via BitPay")

    def ready(self):
        from . import signals   # NOQA

    @cached_property
    def compatibility_errors(self):
        errs = []
        try:
            import bitpay  # NOQA
        except ImportError:
            errs.append("Python package 'bitpay' is not installed.")
        return errs


default_app_config = 'pretix_bitpay.BitpayApp'
