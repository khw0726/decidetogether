1. Overall layout
I want to move the current sidebar to the top of the page to use the horizontal real estate better. The community selection can be in a dropdown at the right end of the top nav bar. The current items should be in a tab-like view, in the following order.

----------------------------------------------------------------------------------------------------------------
     |                          List of Tabs                                             |      (Dropdown)
Logo | Community Profile | Moderation Queue | Rules & Logics Editor | Unlinked Overrides | Community Selector | 
----------------------------------------------------------------------------------------------------------------
2. Merge Rules Editor and Rule Health into a single page named "Rules & Logics Editor"
Currently, the features of editing rules and logics are spread across two pages, making the user flow confusing. I want to merge the two pages, with the following instructions.

The page will be in the following structure. 

-------------------------------------------------------
         |Rule Title                           |test|
         |---------------------------------------------
         | Rule Text   | Automoderator  |     Rule
         |             |    Logic       |    Health
         |             |                |
  Rules  |             |                |
 Sidebar |             |                |
         |             |                |
         |             |                |
         |-----------------------------------------------
         |             
         |                 Decisions
         |             
         |

Here follows the feature of each panel

a. Rules sidebar: Shows the list of current rules. For each rule, it should show the full title, rule type (actionable/procedural/meta/infomational), scope (post/comment/both), and rule health (% of overridden decisions). Plus, there should be a "New" button to add a new rule. 

b. Rule text: Shows the human-readable rule text, relevant contexts, rule type, and the scope of the selected rule. There's an edit button on top of the panel to toggle edit mode, where the user can edit either the rule text, relevant context, type, or scope. When rule text or context is edited, the panel shows a preview button that would show a comparison of automoderator logic before and after edit. 

c. Automoderator Logic: Shows the checklist items.

d. Rule Health: shows the rule health as in rule health page. By default, show the number of instances and the percentage of Wrongly Flagged / Missed items as well as the number of overall decisions at the rule level. When a checklist item is selected from the Automoderator Logic panel, additionally show the health information for the selected item. On top of the panel, keep a "Analyze" button. When it's clicked, suggest potential improvements to the automoderator logic and a comparison of automoderator logic before and after edit. 

e. Decisions: Show the decisions and examples related to the selected rule. When a checklist item is selected, it should filter out to show examples relevant to the selected item only. When the user is previewing the edits in checklist items from Rule text panel or from "Analyze" button of Rule Health, show a preview on how it would change the automod's decision on the previous decisions. 

f. test button: When clicked, show a modal that has the functionality of the test panel --- to test the automod's decision on a hypothetical post
