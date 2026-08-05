.PHONY: help test run demo clean

help:
	@echo "targets:"
	@echo "  test   rm splitwise.db, run app.py self-check (offline)"
	@echo "  run    seed + serve on :5000 (foreground)"
	@echo "  demo   start server in background, curl the endpoints, stop it"
	@echo "  clean  rm splitwise.db"

test:
	rm -f splitwise.db
	python3 app.py test

run:
	python3 app.py

demo:
	@python3 app.py & echo $$! > /tmp/sw_demo.pid; \
	sleep 2; \
	TOKEN=$$(curl -s localhost:5000/auth/login -H 'Content-Type: application/json' \
		-d '{"email":"anna@example.com","password":"password"}' \
		| python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'); \
	AUTH="Authorization: Bearer $$TOKEN"; \
	echo "== logged in as anna@example.com =="; \
	echo "== GET /groups/1/expenses =="; \
	curl -s -H "$$AUTH" localhost:5000/groups/1/expenses; echo; \
	echo "== POST /groups/1/expenses (Snacks) =="; \
	curl -s -X POST -H "$$AUTH" -H 'Content-Type: application/json' localhost:5000/groups/1/expenses \
		-d '{"paid_by":1,"amount":"9.00","split_type":"equal","participants":[1,2,3],"description":"Snacks"}'; echo; \
	echo "== GET /groups/1/settle-up =="; \
	curl -s -H "$$AUTH" localhost:5000/groups/1/settle-up; echo; \
	echo "== POST /groups/1/settlements =="; \
	curl -s -X POST -H "$$AUTH" -H 'Content-Type: application/json' localhost:5000/groups/1/settlements \
		-d '{"from_user":3,"to_user":1,"amount":"35.00"}'; echo; \
	echo "== GET /groups/1/settle-up =="; \
	curl -s -H "$$AUTH" localhost:5000/groups/1/settle-up; echo; \
	kill $$(cat /tmp/sw_demo.pid)

clean:
	rm -f splitwise.db
