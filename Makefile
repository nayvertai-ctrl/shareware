.PHONY: help test check serve deploy

help:
	@echo "targets:"
	@echo "  test    run the Edge Function money-logic tests (offline)"
	@echo "  check   type-check the Edge Function"
	@echo "  serve   serve index.html locally on :5000"
	@echo "  deploy  deploy the Edge Function to Supabase"

test:
	deno test supabase/functions/api/index.test.ts

check:
	deno check supabase/functions/api/index.ts

# index.html is a static file that talks to hosted Supabase, so this is just a
# file server -- there is no local backend to run.
serve:
	python3 -m http.server 5000

deploy:
	deno test supabase/functions/api/index.test.ts
	supabase functions deploy api
