# Support triage agent

An agent that reads a support ticket for Ledgerly (a fictional invoicing product) and takes one of three actions: sends a reply, hands the ticket to a team, or asks the customer a question. The project is mostly about the evaluations that say when it can be trusted to do that without review.

> **Status:** the baseline is built but not yet evaluated. The agent, dataset, evaluators and runner are in place and tested offline. I'll add the before-and-after results, trace links and the write-up of failures once the first runs are done.

## Quickstart

```bash
make setup          # uv sync, and creates .env from .env.example
```

Fill in `ANTHROPIC_API_KEY` and `LANGSMITH_API_KEY` in `.env`, then:

```bash
make test                         # offline tests, no keys needed
make retrieval                    # retrieval recall, no keys needed
make demo CASE=e02                # one ticket, printed step by step
make eval NAME=baseline           # full evaluation as a LangSmith experiment
make report RUNS="baseline v1"    # before-and-after table and the cases that changed
```

## The agent

```mermaid
flowchart LR
    T([ticket]) --> R[redact<br/>card, CVV, bank account]
    R --> A[agent<br/>LLM with tools]
    A -- lookup tools --> X[tools]
    X -- results --> A
    X -- action tool called --> D([decision])
    A -- no tool call --> G[give_up<br/>escalate to support]
    X -- step limit --> G
    G --> D
```

| Tool | Kind | What it does |
|---|---|---|
| `search_help_center` | retrieval | BM25 over the help-centre articles in `data/kb`, split by section |
| `lookup_account` | lookup | The sender's plan, role, subscription and recent charges |
| `send_reply` | action | Sends a reply with the articles it cites |
| `escalate_to_human` | action | Routes to billing, engineering, security, privacy or support |
| `ask_customer` | action | Asks one clarifying question |

These rules are in code, not in the prompt, so they hold whatever the model does:

- **Payment details never reach the model.** Card numbers (checked with the Luhn algorithm), CVVs and bank account numbers are masked before the first model call, so they don't appear in the trace or the reply either.
- **The agent can only look up the sender's account.** `lookup_account` takes no arguments, so a ticket cannot talk it into reading someone else's.
- **Every run ends in a decision.** If the model stops without acting or uses up its steps, the ticket goes to a human.

## The dataset

`data/eval/triage.jsonl` has 45 tickets, each with the expected outcome and a note on why it is there.

| Slice | Cases | What it covers |
|---|---|---|
| normal | 23 | How-to questions the help centre answers, requests that need a team (refunds, takeovers, bugs, privacy), and tickets too vague to act on |
| edge | 14 | The answer depends on the account (plan, role, failed payment), two intents in one ticket, questions the help centre does not cover, text-speak, an angry but simple question |
| adversarial | 8 | Prompt injection, social engineering, a pasted card number, pressure to promise a refund |

Where two outcomes are both acceptable the case lists both, so the checks do not punish a reasonable choice.

## The evaluators

Deterministic checks in `evals/evaluators.py` cover everything that has a right answer:

| Check | Question |
|---|---|
| `safe_action` | Did it avoid replying to a ticket that needed a person? The most costly error. |
| `no_needless_escalation` | Did it avoid escalating a ticket it should have answered? The cost of caution. |
| `action_correct`, `category_correct`, `team_correct`, `priority_correct` | Was the triage right? |
| `retrieval_hit` | Did a search return the article that answers the ticket? |
| `citations_valid`, `cited_expected_doc` | Does the reply cite articles it actually retrieved, and the right one? |
| `account_lookup` | When the answer depends on the account, did it look? |
| `reply_contains`, `no_forbidden_content`, `no_card_number` | Key facts present; no leaked account details, refund promises or card numbers |
| `reached_decision` | Did it decide, or fall through to the give-up path? |

An LLM judge in `evals/judge.py` covers the two questions a string match cannot answer. It only marks replies:

- `grounded`: every claim is in the retrieved text.
- `helpful`: it answers the question in a tone you would send.

The judge writes its reasoning before each verdict.

`safe_action` and `no_needless_escalation` are reported separately because an agent that escalates everything scores 100% on the first and is useless.

## Results

Nothing has run yet. The only numbers so far come from the retrieval check, which needs no model. It searches with the raw ticket text, so the agent should do at least this well when it writes its own queries:

| | recall@1 | recall@4 |
|---|---|---|
| Expected article retrieved (19 cases) | 14/19 | 18/19 |

The one miss at k=4 is `x04`. "My reminders stopped and my bank isn't syncing" shares no words with the failed-payment section that explains it, so the agent only finds that article if it looks up the account first and then searches for what it saw.

## How coding assistants were used

Not written yet. It will be added with the submission.
