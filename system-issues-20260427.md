## Community Setup

1. Setup을 끝나고 나서는 Rules & Logics Editor로 가는데, Moderation Queue로 가는게 더 좋을 것 같아요

2. Calibrate edge cases: 크게 중요하진 않지만, 한번 Compliant/Violating을 고르면 선택을 취소할 수 없는게 걸리네요

3. [Community Context] 전체적으로 텍스트가 매우 길어서…하나하나 읽기에 시간이 좀 걸릴 것 같아요. 왜 그런 context를 줬는지 example을 주면서 알려주는 것은 좋은데, 덜 verbose 할 수 있으면 좋을 것 같네요

4. “generated from the sampled posts.” 라고 하면, sample post 가 더 많아야 하지 않을까요? 또는 moderator 들이 각자 원하는 기준을 적용해야하지 않을까요? 예를 들면 최근 3개월에서 random 하게 hot 한걸 뽑아온다던지… 

5. Rules Fetch / Import 를 따로 단계를 나눠둔 이유가 있을까요? Import 까지 누르긴 했지만, 저는 Fetch 만 해도 임포트가 되었다고 생각해서 좀 헷갈려서요! 


## Moderation Queue

1. Override 이유 넣는 인풋박스에 Placeholder text가 현재는 “Quick note on why you’re overriding (optional)“인데, “Quick note on why you’re overriding the agent’s decision (optional)“이 되어야 더 클리어 할 것 같아요

2. Agent랑 똑같은 초이스를 한다면 (예) Agent: Approve에 저도 Approve를 누른다면) 굳이 confirm이 한번 더 안 떠도 될 것 같아요

3. 마이너한 버그인데, 어떤 포스트를 remove하는데 클릭하자 마자 사라지지 않고 계속 남아있어서 다시 또 Remove 버튼을 클릭하니까 “Confirming”하는 버튼 로딩하는게 떴네요

4. [Filtering] All verdicts, Approve, Remove는 있는데 Review가 없네요. 그리고 필터링이 유저가 아닌 Agent 결정에 따른거란걸 명시해줘야 할 것 같아요

## Rules and Logics Editor

1. r/AskForAnswers Rule-Wide Health 의 계산이 전체적으로 이상합니다. 2 need attention인데 박스안에는 다 0이고, Uncovered violation은 1이고? 이해하기가 힘드네요 

2. [Rule-Wide Health] [Suggested Fixes] Preview가 뭘 보여줄지 모르겠고, Predicted Impact 계산이 뭔가 이상한것 같아요 (하나만 바뀐다고 되어있는데, 둘 다 approve->approve가 되어있는?)

3. [Rule-Wide Health] Analyze가 뭐를 분석할지 예상이 전혀 가지 않아요. Analyze Error Patterns?

4. [Automoderator Logic] 로직의 각각 아이템을 클릭해가면서 health를 확인하는게 불편해서, health에 문제가 있으면 색깔이나 다른 방식으로 표시를 해줬으면 좋겠어요

5. [Automoderator Logic] Context Preview, Analysis Preview가 무슨 차이인지 잘 모르겠고, Context Preview일 때 Current / Preview를 일일히 비교해가면서 뭐가 바뀌었는지 확인해야해서 매우 불편한네요. 사실 스크린이 작아서 한 줄 당 단어가 한두개씩 밖에 없어서 가독성도 떨어지고요. (Image 3)

6. [Automoderator Logic] 이게 제일 크리티컬 한데, Context Preview/Analysis Preview에 들어가서 다시 원래 view로 돌아갈 수 있는 방법이 없어요.
