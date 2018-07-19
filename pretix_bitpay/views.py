import hashlib
import json
import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from bitpay import key_utils

from pretix.base.models import Order, Quota, RequiredAction
from pretix.base.services.locking import LockTimeoutException
from pretix.base.services.orders import mark_order_paid, mark_order_refunded
from pretix.control.permissions import event_permission_required
from pretix.multidomain.urlreverse import eventreverse
from .models import ReferencedBitPayObject
from .payment import BitPay

logger = logging.getLogger(__name__)


@xframe_options_exempt
def redirect_view(request, *args, **kwargs):
    signer = signing.Signer(salt='safe-redirect')
    try:
        url = signer.unsign(request.GET.get('url', ''))
    except signing.BadSignature:
        return HttpResponseBadRequest('Invalid parameter')

    r = render(request, 'pretix_bitpay/redirect.html', {
        'url': url,
    })
    r._csp_ignore = True
    return r


@csrf_exempt
@require_POST
def webhook(request, *args, **kwargs):
    event_json = json.loads(request.body.decode('utf-8'))
    objid = event_json['data']['id']
    try:
        rso = ReferencedBitPayObject.objects.select_related('order', 'order__event').get(reference=objid)
        if rso.order.event != request.event:
            return HttpResponse("Unable to detect event", status=200)
        rso.order.log_action('pretix_bitpay.event', data=event_json)
        return process_invoice(rso.order, objid)
    except ReferencedBitPayObject.DoesNotExist:
        return HttpResponse("Unable to detect event", status=200)


def process_invoice(order, invoice_id):
    prov = BitPay(order.event)
    src = prov.client.get_invoice(invoice_id)

    with transaction.atomic():
        order.refresh_from_db()
        if order.payment_provider != "bitpay":
            return HttpResponse('Invalid order state', status=200)

        if src['status'] == 'new':
            pass
        elif src['status'] in ('paid', 'confirmed', 'complete'):
            if order.status in (Order.STATUS_PENDING, Order.STATUS_EXPIRED):
                try:
                    mark_order_paid(order, user=None)
                except LockTimeoutException:
                    return HttpResponse("Lock timeout, please try again.", status=503)
                except Quota.QuotaExceededException:
                    if not RequiredAction.objects.filter(event=order.event, action_type='pretix_bitpay.overpaid',
                                                         data__icontains=order.code).exists():
                        RequiredAction.objects.create(
                            event=order.event,
                            action_type='pretix_bitpay.overpaid',
                            data=json.dumps({
                                'order': order.code,
                                'invoice': src['id']
                            })
                        )
        elif src['status'] == ('expired', 'invalid'):
            if order.status == Order.STATUS_PAID:
                RequiredAction.objects.create(
                    event=order.event, action_type='pretix_bitpay.refund', data=json.dumps({
                        'order': order.code,
                        'invoice': src['id']
                    })
                )

    return HttpResponse(status=200)


@event_permission_required('can_change_event_settings')
@require_POST
def auth_disconnect(request, **kwargs):
    del request.event.settings.payment_bitpay_token
    request.event.settings.payment_bitpay__enabled = False
    messages.success(request, _('Your BitPay account has been disconnected.'))

    return redirect(reverse('control:event.settings.payment.provider', kwargs={
        'organizer': request.event.organizer.slug,
        'event': request.event.slug,
        'provider': 'bitpay'
    }))


@event_permission_required('can_change_orders')
@require_POST
def refund(request, **kwargs):
    with transaction.atomic():
        action = get_object_or_404(RequiredAction, event=request.event, pk=kwargs.get('id'),
                                   action_type='pretix_bitpay.refund', done=False)
        data = json.loads(action.data)
        action.done = True
        action.user = request.user
        action.save()
        order = get_object_or_404(Order, event=request.event, code=data['order'])
        if order.status != Order.STATUS_PAID:
            messages.error(request, _('The order cannot be marked as refunded as it is not marked as paid!'))
        else:
            mark_order_refunded(order, user=request.user)
            messages.success(
                request, _('The order has been marked as refunded and the issue has been marked as resolved!')
            )

    return redirect(reverse('control:event.order', kwargs={
        'organizer': request.event.organizer.slug,
        'event': request.event.slug,
        'code': data['order']
    }))


class BitPayOrderView:
    def dispatch(self, request, *args, **kwargs):
        try:
            self.order = request.event.orders.get(code=kwargs['order'])
            if hashlib.sha1(self.order.secret.lower().encode()).hexdigest() != kwargs['hash'].lower():
                raise Http404('')
        except Order.DoesNotExist:
            # Do a hash comparison as well to harden timing attacks
            if 'abcdefghijklmnopq'.lower() == hashlib.sha1('abcdefghijklmnopq'.encode()).hexdigest():
                raise Http404('')
            else:
                raise Http404('')
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def pprov(self):
        return self.request.event.get_payment_providers()[self.order.payment_provider]


@method_decorator(xframe_options_exempt, 'dispatch')
class ReturnView(BitPayOrderView, View):
    def get(self, request, *args, **kwargs):
        with transaction.atomic():
            self.order.refresh_from_db()
            if self.order.status == Order.STATUS_PAID:
                return self._redirect_to_order()

            payment_data = json.loads(self.order.payment_info)
            process_invoice(self.order, payment_data['id'])
            return self._redirect_to_order()

    def _redirect_to_order(self):
        if self.request.session.get('payment_bitpay_order_secret') != self.order.secret:
            messages.error(self.request, _('Sorry, there was an error in the payment process. Please check the link '
                                           'in your emails to continue.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        return redirect(eventreverse(self.request.event, 'presale:event.order', kwargs={
            'order': self.order.code,
            'secret': self.order.secret
        }) + ('?paid=yes' if self.order.status == Order.STATUS_PAID else ''))


@event_permission_required('can_change_event_settings')
def auth_start(request, **kwargs):
    if request.event.settings.payment_bitpay_token:
        messages.error(request, _('You are already connected to BitPay.'))
        return redirect(reverse('control:event.settings.payment.provider', kwargs={
            'organizer': request.event.organizer.slug,
            'event': request.event.slug,
            'provider': 'bitpay'
        }))
    request.session['payment_bitpay_auth_event'] = request.event.pk
    sin = key_utils.get_sin_from_pem(request.event.settings.payment_bitpay_pem)
    if request.GET.get('url'):
        url = request.GET.get('url')
    else:
        url = 'https://test.bitpay.com' if 'test' in request.GET else 'https://bitpay.com'
    try:
        r = requests.post(
            url + '/tokens',
            json={
                'label': settings.PRETIX_INSTANCE_NAME,
                'facade': 'merchant',
                'id': sin
            }
        )
    except requests.ConnectionError:
        messages.error(request, _('Communication with BitPay was not successful.'))
    else:
        if r.status_code == 200:
            data = r.json()['data'][0]
            request.event.settings.payment_bitpay_token = data['token']
            request.event.settings.payment_bitpay_url = url
            return redirect(
                url + '/api-access-request?pairingCode=' + data['pairingCode']
            )
        messages.error(request, _('Communication with BitPay was not successful.'))

    return redirect(reverse('control:event.settings.payment.provider', kwargs={
        'organizer': request.event.organizer.slug,
        'event': request.event.slug,
        'provider': 'bitpay'
    }))
