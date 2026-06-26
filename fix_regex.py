"""Fix mangled regex strings in discovery_engine.py"""
path = 'ai_test_asset_center/discovery_engine.py'
c = open(path, encoding='utf-8').read()

# Fix 1: Line 535 — broken findall
c = c.replace(
    'nums = _re2.findall(r\"(\\w+)\"\\s*:\\s*(-?\\d+\\.?\\d*)\', admin_body)',
    'nums = _re2.findall(r\'\"(\\w+)\"\\s*:\\s*(-?\\d+\\.?\\d*)\', admin_body)'
)

# Fix 2: Remove the broken Rule 7.5 block and replace with simple version
old_75 = '''            # Rule 7.5: Cross-role body comparison
            elif admin_ok and viewer_body and admin_body != viewer_body:
                import re as _re3
                admin_nums = dict(_re3.findall(r\"(\\w+)\"'''
new_75 = '''            # Rule 7.5: Cross-role body comparison (admin vs viewer)
            elif admin_ok and viewer_body and len(viewer_body) > 10:
                import re as _re3
                # Compare common keys between admin and viewer responses
                admin_keys = set(re.findall(r'"([^"]+)"', admin_body[:2000]))
                viewer_keys = set(re.findall(r'"([^"]+)"', viewer_body[:2000]))
                if admin_keys and viewer_keys and admin_keys != viewer_keys:
                    verdict = "confirmed"
                    extra_in_admin = admin_keys - viewer_keys
                    extra_in_viewer = viewer_keys - admin_keys
                    actual = "跨角色响应不一致"
                    if extra_in_admin:
                        actual += f" (admin多: {list(extra_in_admin)[:3]})"
                    if extra_in_viewer:
                        actual += f" (viewer多: {list(extra_in_viewer)[:3]})"
                    confidence = 0.75'''

if old_75 in c:
    c = c.replace(old_75, new_75)
    print('Fixed Rule 7.5 block')
else:
    # Remove the broken placeholder
    c = c.replace(
        '            # Rule 7.5: Cross-role body comparison — see Phase78B Semantic Verifier\n            # (cross-role diff handled by SemanticStateVerifier in post-verdict phase)\n',
        new_75 + '\n'
    )
    print('Replaced placeholder with Rule 7.5')

open(path, 'w', encoding='utf-8').write(c)
print('Done')
