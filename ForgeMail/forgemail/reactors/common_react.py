import logging

import tg
from pylons import c, g
from pymongo.bson import ObjectId

from pyforge.lib.decorators import audit, react
from pyforge.lib.helpers import push_config
from pyforge import model as M

from forgemail.lib import util, exc

log = logging.getLogger(__name__)

common_suffix = tg.config.get('forgemail.domain', '.sourceforge.net')
smtp_client = util.SMTPClient()

@audit('search.check_commit')
@react('forgemail.fire')
def fire_ready_emails(routing_key, data):
    M.Mailbox.fire_ready()

@react('forgemail.notify')
def received_notification(routing_key, data):
    g.set_app(data['mount_point'])
    M.Mailbox.deliver(
        data['notification_id'],
        data['artifact_index_id'],
        data['topic'])
    g.publish('react', 'forgemail.fire')

@audit('forgemail.received_email')
def received_email(routing_key, data):
    '''Route messages according to their destination:

    <topic>@<mount_point>.<subproj2>.<subproj1>.<project>.projects.sourceforge.net
    goes to the audit with routing ID
    <plugin name>.mail.<topic>
    '''
    msg = util.parse_message(data['data'])
    user = util.identify_sender(data['peer'], data['mailfrom'], msg)
    log.info('Received email from %s', user)
    # For each of the addrs, determine the project/app and route appropriately
    for addr in data['rcpttos']:
        try:
            topic, project, app = util.parse_address(addr)
            routing_key = topic
            with push_config(c, project=project, app=app):
                if not app.has_access(user, topic):
                    log.info('Access denied for %s to mailbox %s',
                             user, topic)
                else:
                    log.info('Sending message to audit queue %s', topic)
                    if msg['multipart']:
                        msg_hdrs = msg['headers']
                        for part in msg['parts'][1:]:
                            msg = dict(
                                headers=dict(msg_hdrs, **part['headers']),
                                message_id=part['message_id'][0],
                                in_reply_to=part['in_reply_to'],
                                references=part['references'],
                                filename=part['filename'],
                                content_type=part['content_type'],
                                payload=part['payload'],
                                user_id=user._id and str(user._id))
                            g.publish('audit', routing_key, msg,
                                      serializer='yaml')
                    else:
                        g.publish('audit', routing_key,
                                  dict(msg, user_id=user._id and str(user._id)),
                                  serializer='pickle')
        except exc.ForgeMailException, e:
            log.error('Error routing email to %s: %s', addr, e)
        except:
            log.exception('Error routing mail to %s', addr)

@audit('forgemail.send_email')
def send_email(routing_key, data):
    addrs_plain = []
    addrs_html = []
    addrs_multi = []
    # Divide addresses based on preferred email formats
    for addr in data['destinations']:
        if '@' in addr:
            addrs_plain.append(addr)
        else:
            user = M.User.query.get(_id=ObjectId(addr))
            if not user:
                log.warning('Cannot find user with ID %s', addr)
                continue
            addr = user.preferences.email_address
            if not addr and user.email_addresses:
                addr = user.email_addresses[0]
                log.warning('User %s has not set primary email address, using %s',
                            user._id, addr)
            if not addr:
                log.error("User %s has not set any email address, can't deliver",
                          user._id)
            if user.preferences.email_format == 'plain':
                addrs_plain.append(addr)
            elif user.preferences.email_format == 'html':
                addrs_html.append(addr)
            else:
                addrs_multi.append(addr)
    plain_msg = util.encode_email_part(data['text'], 'plain')
    html_text = g.markdown.convert(data['text'])
    html_msg = util.encode_email_part(html_text, 'html')
    multi_msg = util.make_multipart_message(plain_msg, html_msg)
    smtp_client.sendmail(
        addrs_multi,
        data['from'],
        data['reply_to'],
        data['subject'],
        data['message_id'],
        data.get('in_reply_to', None),
        multi_msg)
    smtp_client.sendmail(
        addrs_plain,
        data['from'],
        data['reply_to'],
        data['subject'],
        data['message_id'],
        data.get('in_reply_to', None),
        plain_msg)
    smtp_client.sendmail(
        addrs_html,
        data['from'],
        data['reply_to'],
        data['subject'],
        data['message_id'],
        data.get('in_reply_to', None),
        html_msg)

        
            
    

