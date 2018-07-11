import hashlib
import json
import logging
import urllib
from collections import OrderedDict

import requests
from bitpay.client import Client
from bitpay.exceptions import BitPayConnectionError, BitPayBitPayError, BitPayArgumentError
from bitpay import key_utils
from django import forms
from django.contrib import messages
from django.core import signing
from django.template.loader import get_template
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.base.services.orders import mark_order_refunded
from pretix.multidomain.urlreverse import build_absolute_uri
from .models import ReferencedBitPayObject

logger = logging.getLogger(__name__)


class RefundForm(forms.Form):
    auto_refund = forms.ChoiceField(
        initial='auto',
        label=_('Refund automatically?'),
        choices=(
            ('auto', _('Automatically refund charge with BitPay')),
            ('manual', _('Do not send refund instruction to BitPay, only mark as refunded in pretix'))
        ),
        widget=forms.RadioSelect,
    )


class BitPay(BasePaymentProvider):
    identifier = 'bitpay'
    verbose_name = _('BitPay')
    public_name = _('Bitcoin')

    def settings_content_render(self, request):
        if not self.settings.token:
            return (
                "<p>{}</p>"
                "<p>"
                "<a href='{}' target='_blank' class='btn btn-primary btn-lg'>{}</a> "
                "<a href='{}' target='_blank' class='btn btn-default btn-lg'>{}</a> "
                "</p>"
                "<p>{}</p>"
                "</form>" # This is a hell of a hack, sorry…
                "<form class='form-inline' action='{}' method='GET' target='_blank'>"
                "<input type='text' name='url' class='form-control' value='https://btcpay.lightbo.lt'> "
                "<button class='btn btn-default'>{}</button>"
                "</form>"
                "<form>"
            ).format(
                _('To accept payments via BitPay, you will need an account at BitPay. By clicking on the '
                  'following button, you can connect pretix to your BitPay account. A BitPay site will open in a new '
                  'tab. Return to this page and refresh it after you authorized the token at BitPay.'),
                reverse('plugins:pretix_bitpay:auth.start', kwargs={
                    'organizer': self.event.organizer.slug,
                    'event': self.event.slug,
                }),
                _('Connect with BitPay'),
                reverse('plugins:pretix_bitpay:auth.start', kwargs={
                    'organizer': self.event.organizer.slug,
                    'event': self.event.slug,
                }) + '?test=1',
                _('Connect with test.bitpay.com'),
                _('Alternatively, you can connect with a third-party provider using a BitPay-compatible API. Enter'
                  'the URL here you want to connect to.'),
                reverse('plugins:pretix_bitpay:auth.start', kwargs={
                    'organizer': self.event.organizer.slug,
                    'event': self.event.slug,
                }),
                _('Start pairing')
            )
        else:
            return (
                "<button formaction='{}' class='btn btn-danger'>{}</button>"
            ).format(
                reverse('plugins:pretix_bitpay:auth.disconnect', kwargs={
                    'organizer': self.event.organizer.slug,
                    'event': self.event.slug,
                }),
                _('Disconnect from BitPay')
            )

    @property
    def settings_form_fields(self):
        if not self.settings.token:
            return {}
        d = OrderedDict(
            [
                ('url',
                 forms.CharField(
                     label=_('API URL'),
                     disabled=True
                 )),
            ] + list(super().settings_form_fields.items())
        )
        d.move_to_end('_enabled', last=False)
        return d

    def redirect(self, request, url):
        if request.session.get('iframe_session', False):
            signer = signing.Signer(salt='safe-redirect')
            return (
                build_absolute_uri(request.event, 'plugins:pretix_bitpay:redirect') + '?url=' +
                urllib.parse.quote(signer.sign(url))
            )
        else:
            return str(url)

    def payment_form_render(self, request) -> str:
        template = get_template('pretix_bitpay/checkout_payment_form.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings}
        return template.render(ctx)

    def checkout_confirm_render(self, request) -> str:
        template = get_template('pretix_bitpay/checkout_payment_confirm.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings}
        return template.render(ctx)

    def checkout_prepare(self, request, total):
        return True

    def payment_is_valid_session(self, request):
        return True

    @cached_property
    def client(self):
        return Client(api_uri=self.settings.url, pem=self.settings.pem)

    def payment_perform(self, request, order) -> str:
        request.session['payment_bitpay_order_secret'] = order.secret

        try:
            inv = self.client.create_invoice({
                "price": float(order.total),
                "currency": self.event.currency,
                "orderId": order.full_code,
                "transactionSpeed": "medium",
                "extendedNotifications": "true",
                "notificationURL": build_absolute_uri(self.event, "plugins:pretix_bitpay:webhook"),
                "redirectURL": build_absolute_uri(self.event, "plugins:pretix_bitpay:return", kwargs={
                    'order': order.code,
                    'hash': hashlib.sha1(order.secret.lower().encode()).hexdigest(),
                }),
                # "buyer": {"email": "test@customer.com"},
                "token": self.settings.token
            })
        except (BitPayConnectionError, BitPayBitPayError, BitPayArgumentError) as e:
            logger.exception('Failure during bitpay payment.')
            raise PaymentException(_('We had trouble communicating with BitPay. Please try again and get in touch '
                                     'with us if this problem persists.'))
        ReferencedBitPayObject.objects.get_or_create(order=order, reference=inv['id'])
        order.payment_info = json.dumps(inv)
        order.save(update_fields=['payment_info'])
        return self.redirect(request, inv['url'])

    def order_pending_render(self, request, order) -> str:
        retry = True
        try:
            if order.payment_info and json.loads(order.payment_info)['paymentState'] == 'PENDING':
                retry = False
        except KeyError:
            pass
        template = get_template('pretix_bitpay/pending.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'retry': retry, 'order': order}
        return template.render(ctx)

    def order_control_render(self, request, order) -> str:
        if order.payment_info:
            payment_info = json.loads(order.payment_info)
        else:
            payment_info = None
        template = get_template('pretix_bitpay/control.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'payment_info': payment_info, 'order': order, 'provname': self.verbose_name}
        return template.render(ctx)

    def order_can_retry(self, order):
        return True

    @property
    def refund_available(self):
        return True

    def _refund_form(self, request):
        return RefundForm(data=request.POST if request.method == "POST" else None)

    def order_control_refund_render(self, order, request) -> str:
        if self.refund_available:
            template = get_template('pretix_bitpay/control_refund.html')
            ctx = {
                'request': request,
                'form': self._refund_form(request),
            }
            return template.render(ctx)
        else:
            return super().order_control_refund_render(order, request)

    def _refund(self, payment_info, order):
        payload = json.dumps({
            'token': payment_info.get('token'),
            'amount': payment_info.get('price'),
            'currency': payment_info.get('currency'),
            'refundEmail': order.email
        })
        uri = self.client.uri + "/invoices/" + payment_info.get('id') + "/refunds"
        xidentity = key_utils.get_compressed_public_key_from_pem(self.client.pem)
        xsignature = key_utils.sign(uri + payload, self.client.pem)
        headers = {"content-type": "application/json", 'accept': 'application/json', 'X-Identity': xidentity,
                   'X-Signature': xsignature, 'X-accept-version': '2.0.0'}
        try:
            response = requests.post(uri, data=payload, headers=headers, verify=self.client.verify)
        except Exception:
            logger.exception('Failure during bitpay refund.')
            raise PaymentException(_('We had trouble communicating with BitPay. Please try again and get in touch '
                                     'with us if this problem persists.'))
        if not response.ok:
            e = response.json()['error']
            logger.exception('Failure during bitpay refund: {}'.format(e))
            raise PaymentException(_('BitPay reported an error: {}').format(e))

    def order_control_refund_perform(self, request, order) -> "bool|str":
        if order.payment_info:
            payment_info = json.loads(order.payment_info)
        else:
            payment_info = None

        if not payment_info or not self.refund_available:
            mark_order_refunded(order, user=request.user)
            messages.warning(request, _('We were unable to transfer the money back automatically. '
                                        'Please get in touch with the customer and transfer it back manually.'))
            return

        f = self._refund_form(request)
        if not f.is_valid():
            messages.error(request, _('Your input was invalid, please try again.'))
            return
        elif f.cleaned_data.get('auto_refund') == 'manual':
            order = mark_order_refunded(order, user=request.user)
            order.payment_manual = True
            order.save()
            return

        try:
            self._refund(
                payment_info, order
            )
        except PaymentException as e:
            messages.error(request, str(e))
        except requests.exceptions.RequestException as e:
            logger.exception('BitPay error: %s' % str(e))
            messages.error(request, _('We had trouble communicating with BitPay. Please try again and contact '
                                      'support if the problem persists.'))
        else:
            mark_order_refunded(order, user=request.user)
