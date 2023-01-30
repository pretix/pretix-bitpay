from django.apps import AppConfig
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from . import __version__


class BitpayApp(AppConfig):
    name = 'pretix_bitpay'
    verbose_name = _("BitPay")

    class PretixPluginMeta:
        name = _("BitPay")
        author = "Raphael Michel"
        category = 'PAYMENT'
        version = __version__
        compatibility = "pretix>=4.16.0"
        description = _("Receive Crypto payments via BitPay-compatible payment providers.")
        picture = "pretix_bitpay/bitpay-logo.svg"

    def ready(self):
        from . import signals  # NOQA

    @cached_property
    def compatibility_errors(self):
        errs = []
        try:
            import btcpay  # NOQA
        except ImportError:
            errs.append("Python package 'btcpay' is not installed.")
        return errs


