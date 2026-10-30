// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title OnsummAnchor
/// @notice Stores daily Merkle roots of Onsumm's audit log. Holds no funds.
contract OnsummAnchor {
    struct Anchor {
        bytes32 root;
        uint64 fromSeq;
        uint64 toSeq;
        uint64 timestamp;
    }

    address public owner;
    address public anchorer;
    uint256 public count;
    mapping(uint256 => Anchor) public anchors;

    event Anchored(uint256 indexed id, bytes32 root, uint64 fromSeq, uint64 toSeq, uint64 timestamp);
    event AnchorerChanged(address indexed previous, address indexed next);

    error NotOwner();
    error NotAnchorer();
    error EmptyRoot();
    error BadRange();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyAnchorer() {
        if (msg.sender != anchorer) revert NotAnchorer();
        _;
    }

    constructor(address initialAnchorer) {
        owner = msg.sender;
        anchorer = initialAnchorer;
        emit AnchorerChanged(address(0), initialAnchorer);
    }

    function setAnchorer(address next) external onlyOwner {
        emit AnchorerChanged(anchorer, next);
        anchorer = next;
    }

    function anchor(bytes32 root, uint64 fromSeq, uint64 toSeq) external onlyAnchorer returns (uint256 id) {
        if (root == bytes32(0)) revert EmptyRoot();
        if (toSeq < fromSeq) revert BadRange();
        id = ++count;
        uint64 ts = uint64(block.timestamp);
        anchors[id] = Anchor(root, fromSeq, toSeq, ts);
        emit Anchored(id, root, fromSeq, toSeq, ts);
    }
}
