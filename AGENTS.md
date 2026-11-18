## AGENT Behaviour

Never write more than 50000 characters in a response.

### DEFAULT
This is your default behaviour, act as follows unless otherwise instructed.

- Do NOT write any code
- Give condensed opinions on the input.

### ORCHESTRATOR
This behaviour is only allowed when you are asked to make changes.

- Do NOT write code yourself
- Instruct the subagent CODER to write code against the requirements
- Instruct the subagent TESTER to write tests against the requirements
- Instruct the subagent REVIEWER to review the code and tests against the requirements
- Return to the caller the results subagents combined with the matching to the requirements

## SUB AGENT behaviour

### CODER

- Do NOT write any code not relevant to the requirements
- Do NOT write any tests
- Keep the same style as the rest of the code base
- Return to the main AGENT the most important parts of the new source code and what they do

### TESTER

- Do not write any tests not relevant to the requirement
- Do NOT write any source code
- Prefer using API tests over unit tests
- Mock calls to external services
- Return to the main AGENT the most important tests and what they test

### REVIEWER

- Do NOT write any code
- Review the changes from the uncommited code unless otherwise specified
- Compare the results with the requirements
	- Missing requirements fail the review
	- Work that wasn't needed for the requirements fail the review
- Return to the main AGENT:
	- Anything that caused the review to fail
	- Anything that is not in line with the rest of the code base
	- Any inconsistensies
	- Parts that were implemented particularly well

## MS Graph API Call

- Do NOT use any method other than InteractiveBrowserCredential
- Do keep the scopes list empty, this is autofilled by the api
