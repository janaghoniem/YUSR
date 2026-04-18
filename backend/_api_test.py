import asyncio, sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

async def main():
    from agents.email_agent import EmailAgent
    agent = EmailAgent()
    user_id = 'hala'

    print('\n==============================')
    print('  TEST 1: YouTube Search API  ')
    print('==============================')
    try:
        r = await agent.youtube_search(user_id=user_id, query='python tutorials', max_results=3)
        if r.status == 'success':
            videos = r.result if isinstance(r.result, list) else (r.result.get('videos', []) if isinstance(r.result, dict) else [])
            print(f'STATUS: SUCCESS - {len(videos)} results')
            for v in videos:
                if isinstance(v, dict):
                    print(f'  Title   : {v.get("title")}')
                    print(f'  URL     : {v.get("url")}')
                    print(f'  Views   : {v.get("view_count")}')
                    print()
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result type: {type(r.result)}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 2: Gmail - Read Emails ')
    print('==============================')
    try:
        r = await agent.read_unread_emails(user_id=user_id, max_results=3)
        if r.status == 'success':
            emails = r.result if isinstance(r.result, list) else (r.result.get('emails', []) if isinstance(r.result, dict) else [])
            print(f'STATUS: SUCCESS - {r.message_count} unread emails')
            for em in (emails[:3] if isinstance(emails, list) else []):
                if isinstance(em, dict):
                    print(f'  From   : {em.get("from") or em.get("sender")}')
                    print(f'  Subject: {em.get("subject")}')
                    print(f'  Date   : {em.get("date") or em.get("received_at")}')
                    print()
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result type: {type(r.result)}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 3: Gmail - Send Email  ')
    print('==============================')
    try:
        r = await agent.send_email(
            user_id=user_id,
            to='aura31085@gmail.com',
            subject='YUSR API Test - April 2026',
            body='This is an automated test email from YUSR backend API test suite. If you received this, the Gmail Send API is working correctly.'
        )
        if r.status == 'success':
            print(f'STATUS: SUCCESS')
            res = r.result if isinstance(r.result, dict) else {}
            print(f'  Message ID: {res.get("message_id") or res.get("id")}')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print("  TEST 4: Calendar - Create   ")
    print('==============================')
    try:
        r = await agent.calendar_create(
            user_id=user_id,
            title="Mariam's Birthday",
            start_time='2026-06-20T00:00:00',
            end_time='2026-06-20T23:59:59',
            description='Happy Birthday Mariam!'
        )
        if r.status == 'success':
            res = r.result if isinstance(r.result, dict) else {}
            ev = res.get('event', res)  # result may be {'event': {...}} or the event dict directly
            print(f'STATUS: SUCCESS')
            print(f'  Event ID: {ev.get("id")}')
            print(f'  Title   : {ev.get("summary")}')
            print(f'  Start   : {ev.get("start")}')
            print(f'  End     : {ev.get("end")}')
            print(f'  Link    : {ev.get("htmlLink")}')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 5: Calendar - List     ')
    print('==============================')
    try:
        r = await agent.calendar_list(user_id=user_id, max_results=5)
        if r.status == 'success':
            result = r.result
            events = result if isinstance(result, list) else (result.get('events', []) if isinstance(result, dict) else [])
            print(f'STATUS: SUCCESS - {len(events)} upcoming events')
            for ev in (events if isinstance(events, list) else []):
                if isinstance(ev, dict):
                    print(f'  Title: {ev.get("summary")}')
                    print(f'  Start: {ev.get("start")}')
                    print()
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:400]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 6: YouTube Video Info  ')
    print('==============================')
    try:
        r = await agent.youtube_video_info(user_id=user_id, video_url='https://www.youtube.com/watch?v=b093aqAZiPU')
        if r.status == 'success':
            info = r.result if isinstance(r.result, dict) else {}
            print(f'STATUS: SUCCESS')
            print(f'  Title      : {info.get("title")}')
            print(f'  Channel    : {info.get("channel")}')
            print(f'  Views      : {info.get("view_count")}')
            print(f'  Likes      : {info.get("like_count")}')
            print(f'  Published  : {info.get("published_at")}')
            print(f'  Description: {str(info.get("description",""))[:100]}...')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 7: Extract OTP Codes   ')
    print('==============================')
    try:
        r = await agent.extract_otp_codes(user_id=user_id, max_results=5)
        if r.status == 'success':
            result = r.result
            codes = result if isinstance(result, list) else (result.get('otp_codes', result.get('codes', [])) if isinstance(result, dict) else [])
            print(f'STATUS: SUCCESS - {len(codes) if isinstance(codes, list) else "?"} OTP(s) found')
            if isinstance(codes, list):
                for c in codes:
                    print(f'  Code: {c.get("code") if isinstance(c, dict) else c}  From: {c.get("from","") if isinstance(c, dict) else ""}')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 8: Extract Magic Links ')
    print('==============================')
    try:
        r = await agent.extract_magic_links(user_id=user_id, max_results=5)
        if r.status == 'success':
            result = r.result
            links = result if isinstance(result, list) else (result.get('magic_links', result.get('links', [])) if isinstance(result, dict) else [])
            print(f'STATUS: SUCCESS - {len(links) if isinstance(links, list) else "?"} link(s) found')
            if isinstance(links, list):
                for lnk in links:
                    print(f'  Link: {str(lnk.get("url","") if isinstance(lnk, dict) else lnk)[:80]}')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

    print('\n==============================')
    print('  TEST 9: Google Drive List   ')
    print('==============================')
    try:
        r = await agent.drive_list(user_id=user_id, max_results=5)
        if r.status == 'success':
            result = r.result
            files = result if isinstance(result, list) else (result.get('files', []) if isinstance(result, dict) else [])
            print(f'STATUS: SUCCESS - {len(files) if isinstance(files, list) else "?"} file(s)')
            if isinstance(files, list):
                for f in files:
                    if isinstance(f, dict):
                        print(f'  Name: {f.get("name")}  Type: {f.get("mimeType","").split(".")[-1]}  ID: {f.get("id")}')
        else:
            print(f'STATUS: FAIL - {r.error}')
        print(f'  Raw result: {str(r.result)[:300]}')
    except Exception as e:
        print(f'STATUS: ERROR - {e}')
        import traceback; traceback.print_exc()

asyncio.run(main())
