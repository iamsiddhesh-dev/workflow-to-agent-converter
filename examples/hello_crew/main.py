"""Smoke test 2 (Phase 1.3): a hand-written 2-agent CrewAI crew running on the
free-tier LLM. Proves the generation *target* actually executes on the
zero-cost stack before anything generates it.

Run: python examples/hello_crew/main.py
"""

from __future__ import annotations

import os

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv

load_dotenv()


def build_llm() -> LLM:
    return LLM(
        model=f"gemini/{os.environ.get('W2A_GEMINI_MODEL', 'gemini-flash-lite-latest')}",
        api_key=os.environ["GEMINI_API_KEY"],
        temperature=0.3,
    )


def main() -> None:
    llm = build_llm()

    researcher = Agent(
        role="Researcher",
        goal="Find three concrete, non-obvious facts about the given topic",
        backstory="A meticulous analyst who only reports facts they can state plainly.",
        llm=llm,
        verbose=True,
    )

    writer = Agent(
        role="Writer",
        goal="Turn research notes into a short, punchy paragraph",
        backstory="A copywriter who values clarity over flourish.",
        llm=llm,
        verbose=True,
    )

    research_task = Task(
        description="Research three concrete facts about the topic: {topic}.",
        expected_output="A bullet list of exactly three facts.",
        agent=researcher,
    )

    write_task = Task(
        description="Using the research notes, write a single short paragraph (3-4 sentences) about {topic}.",
        expected_output="One short paragraph, no bullet points.",
        agent=writer,
        context=[research_task],
    )

    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, write_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff(inputs={"topic": "the history of the printing press"})
    print("\n--- FINAL OUTPUT ---")
    print(result.raw)


if __name__ == "__main__":
    main()
