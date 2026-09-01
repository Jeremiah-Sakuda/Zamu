"use client";

import type { OutboxMessage } from "@/lib/api";
import { Card, Empty } from "./ui";

/**
 * The volunteer's inbox, exposed inside the sandbox.
 *
 * This is not a debug view: without it a single person cannot drive the loop end to
 * end, and a demo nobody can drive is a demo nobody believes. In a real deployment
 * the messages go to real inboxes and this screen simply shows what was sent.
 */
export function OutboxPanel({ messages }: { messages: OutboxMessage[] }) {
  return (
    <section aria-labelledby="outbox-heading">
      <div className="mb-3">
        <h2 id="outbox-heading" className="text-lg font-bold tracking-tight">
          What Zamu sent
        </h2>
        <p className="text-sm text-muted-foreground">
          One message per person per shift, answerable in one tap. In this sandbox the
          links are live, so you can answer as the volunteer and watch the roster change.
        </p>
      </div>

      {messages.length === 0 ? (
        <Empty>Zamu has not messaged anybody.</Empty>
      ) : (
        <ul className="space-y-3">
          {messages.map((message, i) => (
            <Card as="li" key={`${message.ask_id}-${i}`} className="p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-bold">
                  To {message.to_name}{" "}
                  <span className="font-normal text-muted-foreground">
                    &lt;{message.to_email}&gt;
                  </span>
                </h3>
                {message.state ? (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-bold text-muted-foreground">
                    {message.state}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-sm font-bold">{message.subject}</p>

              <details className="mt-2">
                <summary className="cursor-pointer text-sm text-muted-foreground">
                  Read the message
                </summary>
                <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs">
                  {message.text}
                </pre>
              </details>

              {message.state === "sent" && message.accept_url ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <a
                    className="inline-flex min-h-11 items-center rounded-lg border-2 border-accent bg-accent px-4 py-2 text-sm font-bold text-on-accent transition-[filter] duration-200 hover:brightness-110"
                    href={message.accept_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Answer yes as {message.to_name.split(" ")[0]}
                  </a>
                  <a
                    className="inline-flex min-h-11 items-center rounded-lg border-2 border-primary px-4 py-2 text-sm font-bold transition-colors duration-200 hover:bg-primary hover:text-on-primary"
                    href={message.decline_url ?? "#"}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Answer no
                  </a>
                </div>
              ) : null}

              {!message.delivered ? (
                <p className="mt-2 text-sm text-uncovered">Not delivered: {message.detail}</p>
              ) : null}
            </Card>
          ))}
        </ul>
      )}
    </section>
  );
}
